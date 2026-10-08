"""Hermes Agent as Jarvis's brain: every turn goes to Hermes, which orchestrates.

Hermes Agent (Nous Research, MIT) is an agent with its own model providers,
tools, MCP servers, memory, skills and subagents. Its gateway exposes an API
server (``hermes gateway`` with ``API_SERVER_ENABLED``, ``http://127.0.0.1:8642``
by default). This brain hands each turn to it as one Hermes run
(``POST /v1/runs``, events from ``/v1/runs/{id}/events``), so the flow is::

    user -> Jarvis (voice, UI) -> Hermes -> Hermes picks the model
         (local Qwen, local Gemma, or a configured cloud model) -> Hermes tools
         -> answer -> Jarvis -> user

Jarvis does not offer Hermes its own tools and does not pick the model. The
model is whatever Hermes resolves for the request: its configured default, its
fallback providers, or a ``model_routes`` alias named on this card (``qwen``,
``gemma``). A card value ``provider::model`` asks for one Hermes provider
directly (Hermes always honours an explicit provider). The model Hermes actually
used comes back as ``runtime`` and is logged and kept on ``last_runtime``.

Because Hermes runs the tools, the manager treats it as the owner of the turn
(``orchestrates_tools``) and its own shortcuts stand down. The tools Hermes
reports completing successfully (``tool.completed`` events) are passed on as
``BrainDelta.agent_tools`` so Jarvis's honesty guard can still tell a real
action from a promise.

One conversation: every voice and text turn is a run in the same Hermes
session (``session_id``), so Hermes keeps the history and Jarvis sends only the
newest turn. That also lets Hermes run a delegated task in the background; when
its result lands in the session, Jarvis asks Hermes for it in the same session
and speaks it.

Approvals stay Hermes's: when Hermes stops a command for approval
(``approval.request``), Jarvis speaks the question and ends the turn while the
run waits. The user's next answer resolves that same run
(``POST /v1/runs/{id}/approval``): yes runs it once, no denies it, and anything
unrelated denies it before the new turn starts. A turn the user cuts off stops
the run (``POST /v1/runs/{id}/stop``).

Credentials: none are stored in Jarvis. The API server key is read from
Hermes's own ``.env`` (``API_SERVER_KEY``) when the server is on this machine;
a remote Hermes is refused rather than sent a key it never asked for.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from jarvis.core import config as cfg
from jarvis.core.agent_turn import AgentPolicyError
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest

from .cli_prompt_context import (
    extract_reply_language_directive,
    render_cli_standing_instructions,
)

log = logging.getLogger(__name__)

#: Hermes's API server default (``API_SERVER_PORT``), loopback only.
DEFAULT_BASE_URL = "http://127.0.0.1:8642"

#: The model name Hermes advertises for itself; it means "use Hermes's own pick".
AGENT_MODEL = "hermes-agent"

#: Separates a Hermes provider slug from a model id on the card (``local-qwen::qwen``).
PROVIDER_SEPARATOR = "::"

#: Stable memory scope for Hermes (``X-Hermes-Session-Key``): every Jarvis turn
#: shares one long-term memory lane, like one chat on a messaging platform.
SESSION_KEY = "jarvis:main"

#: The one Hermes session every Jarvis conversation turn continues, spoken or
#: typed: Hermes loads the history from its own store. Only BrainManager's
#: conversation brain is put on it (``BrainManager._get_brain``).
SESSION_ID = "jarvis-main"

#: Where every other Hermes call goes: wiki curation, learning review, goal
#: checks, provider probes. The person's conversation never carries Jarvis's
#: own housekeeping prompts.
BACKGROUND_SESSION_ID = "jarvis-background"


@dataclass(slots=True)
class _SessionState:
    """What belongs to a Hermes session, not to one HermesBrain object.

    Voice and the typed chat build separate brain objects for the same
    session; an approval asked in one is answered in the other, and one
    background result is announced once.
    """

    pending_approval: _PendingApproval | None = None
    watcher: asyncio.Task[None] | None = None
    runs: dict[str, _OpenRun] = field(default_factory=dict)
    delegations: dict[str, str] = field(default_factory=dict)
    control_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_SESSIONS: dict[str, _SessionState] = {}


def _session_state(session_id: str) -> _SessionState:
    return _SESSIONS.setdefault(session_id, _SessionState())

#: Hermes's delegation tool; its background result lands in the session.
DELEGATE_TOOL = "delegate_task"
#: How a finished background delegation is stored in the session transcript.
DELIVERY_KIND = "async_delegation_complete"
DELIVERY_POLL_S = 5.0
#: How long Jarvis watches for one background result before giving up.
DELIVERY_WAIT_S = 1800.0
#: The turn Jarvis takes in the session when a background result has landed.
DELIVERY_PROMPT = (
    "The task you delegated in the background has finished. Tell me its result "
    "now, in one to three short spoken sentences."
)

#: Unclear answers to one approval question before Jarvis denies it.
MAX_APPROVAL_REASKS = 2
#: Wait for the rest of a Hermes run the user moved away from.
DRAIN_WAIT_S = 30.0
_DESTRUCTIVE_WORDS = re.compile(r"delet|remov|\brm\b|wipe|format|erase|drop", re.IGNORECASE)

#: Spoken-front-end contract layered on top of Hermes's own system prompt.
FRONT_END_INSTRUCTIONS = (
    "You are answering through JARVIS, the user's voice and desktop front-end. "
    "Your reply is spoken aloud: lead with the answer, in one to three short "
    "sentences of plain text, no markdown, no lists, unless the user asks for "
    "detail. Use your own tools to act on the computer, the browser and files. "
    "Say you did something only after a tool actually did it."
)

#: Added when the user asked only to look ("don't click anything, just look").
OBSERVE_ONLY_INSTRUCTIONS = (
    "This turn is observation only: the user asked you to look, not to act. "
    "You may capture or read the screen, a page or a file, but do not click, "
    "type, press keys, open, close, launch, send, write or change anything. "
    "Describe what you see."
)


def _hermes_home_candidates() -> list[Path]:
    homes: list[Path] = []
    env_home = os.environ.get("HERMES_HOME", "").strip()
    if env_home:
        homes.append(Path(env_home))
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        homes.append(Path(local) / "hermes")
    homes.append(Path.home() / ".hermes")
    return homes


def read_api_server_key(homes: list[Path] | None = None) -> str | None:
    """``API_SERVER_KEY`` from Hermes's own ``.env``; ``None`` when unset.

    Read in place on every client build and never copied or logged, so the
    key stays where Hermes keeps it.
    """
    for home in homes if homes is not None else _hermes_home_candidates():
        env_file = home / ".env"
        try:
            lines = env_file.read_text(encoding="utf-8-sig").splitlines()
        except OSError:  # no .env in this Hermes home; try the next one
            continue
        for line in lines:
            key, sep, value = line.strip().partition("=")
            if sep and key.strip() == "API_SERVER_KEY":
                cleaned = value.strip().strip('"').strip("'")
                if cleaned:
                    return cleaned
    return None


def thinking_off_by_config() -> bool:
    """Whether ``[brain.providers.hermes].thinking_budget = 0`` is set."""
    try:
        provider = cfg.load_config().brain.providers.get("hermes")
    except Exception:  # noqa: BLE001 â€” an unreadable config keeps Hermes's own reasoning setting
        return False
    return provider is not None and getattr(provider, "thinking_budget", None) == 0


def configured_base_url() -> str:
    """The card's server URL, else Hermes's default API server address."""
    try:
        provider = cfg.load_config().brain.providers.get("hermes")
    except Exception:  # noqa: BLE001 â€” an unreadable config falls back to the default address
        provider = None
    raw = (getattr(provider, "base_url", "") or "").strip() if provider is not None else ""
    from .ollama import normalize_server_root

    return normalize_server_root(raw or DEFAULT_BASE_URL)


def model_fields(model: str | None) -> dict[str, str]:
    """The run's ``model``/``provider`` for one card value.

    ``""`` or ``hermes-agent`` leaves the choice to Hermes; ``alias`` names a
    Hermes ``model_routes`` entry; ``provider::model`` asks one Hermes provider
    for one model.
    """
    value = (model or "").strip()
    if not value or value == AGENT_MODEL:
        return {"model": AGENT_MODEL}
    provider, sep, model_id = value.partition(PROVIDER_SEPARATOR)
    if sep and provider.strip() and model_id.strip():
        return {"model": model_id.strip(), "provider": provider.strip()}
    return {"model": value}


def build_instructions(req: BrainRequest) -> str:
    """The front-end contract layered on Hermes's own system prompt.

    Jarvis's own router prompt (its tool rules and tool names) is not sent:
    Hermes has its own tools and would chase names it does not have. The
    user's standing preferences and the turn's reply language are kept.
    """
    parts = [FRONT_END_INSTRUCTIONS]
    prefs = render_cli_standing_instructions(req.system)
    if prefs:
        parts.append(prefs)
    lang = extract_reply_language_directive(req.system)
    if lang:
        parts.append(lang)
    if observation_only(latest_user_text(req)):
        parts.append(OBSERVE_ONLY_INSTRUCTIONS)
    return "\n\n".join(parts)


def observation_only(text: str) -> bool:
    """Whether the user asked only to look (Jarvis's own look-not-act gate)."""
    from jarvis.brain.cu_gate import is_observation_only

    return bool(text) and is_observation_only(text)


def latest_user_message(req: BrainRequest) -> BrainMessage | None:
    for message in reversed(req.messages):
        if getattr(message, "role", None) == "user":
            return message
    return None


def run_input(req: BrainRequest) -> str | list[dict[str, Any]]:
    """The run's ``input``: the newest user text, with its images when it has any.

    Images go as OpenAI ``image_url`` parts in one user message; Hermes hands
    them to a model that can see, or describes them with its own vision tool
    when the model cannot. Whether an image may leave the machine at all is
    decided before this brain is picked (``route_policy.media_chain``).
    """
    text = latest_user_text(req)
    message = latest_user_message(req)
    images = tuple(getattr(message, "images", ()) or ()) if message is not None else ()
    if not images:
        return text
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    for image in images:
        url = f"data:{image.mime};base64,{image.data_b64}"
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return [{"role": "user", "content": parts}]


def latest_user_text(req: BrainRequest) -> str:
    for message in reversed(req.messages):
        if getattr(message, "role", None) == "user" and isinstance(message.content, str):
            return message.content
    return ""


def phrase_language() -> str:
    """The language for Jarvis's own approval phrases (de/en/es)."""
    from jarvis.core.turn_language import DEFAULT_LOCALE

    try:
        pinned = str(cfg.load_config().brain.reply_language or "")
    except Exception:  # noqa: BLE001 â€” an unreadable config speaks the default language
        pinned = ""
    return pinned if pinned in ("de", "en", "es") else DEFAULT_LOCALE


def approval_question(payload: dict[str, Any], language: str) -> str:
    """Jarvis's spoken question for one Hermes ``approval.request``."""
    from jarvis.voice.tool_confirmation import format_tool_confirmation

    what = str(payload.get("description") or payload.get("command") or "").strip()
    level = "destructive" if _DESTRUCTIVE_WORDS.search(what) else "modify"
    return format_tool_confirmation(
        "hermes", language=language, impact_level=level, impact_commands=what
    )


def tool_name_of(payload: Any) -> str:
    """The tool name in a ``tool.started`` event (several spellings)."""
    if not isinstance(payload, dict):
        return ""
    for key in ("tool", "name", "tool_name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = value.get("name")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return ""


#: End of one run's event stream on its queue.
_END = object()

#: Terminal run events.
_FINISHED = frozenset({"run.completed", "run.failed", "run.cancelled", "run.interrupted"})


@dataclass
class _OpenRun:
    """One Hermes run whose event stream a background task reads into ``queue``."""

    base_url: str
    run_id: str
    queue: asyncio.Queue[Any]
    task: asyncio.Task[None] | None = None
    finished: bool = False
    stop_requested: bool = False
    close_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    open_tools: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class _PendingApproval:
    """A Hermes run parked on an approval the user was asked about."""

    run: _OpenRun
    request_id: str
    language: str
    reasks: int = field(default=0)


async def _single(text: str) -> AsyncIterator[BrainDelta]:
    yield BrainDelta(content=text)
    yield BrainDelta(finish_reason="stop")


class HermesBrain:
    """Brain that hands the whole turn to Hermes Agent as one run in one session."""

    name: str = "hermes"
    context_window: int = 128_000
    # Tool turns are served: Hermes calls its own tools. Jarvis's tool list is
    # never sent, so no Jarvis tool call can come back.
    supports_tools: bool = True
    # Images in the turn are forwarded (``run_input``): Hermes sends them to a
    # model that can see or describes them with its own vision tool, and says
    # so when neither works. Screen Context stays off for Hermes, which looks
    # with its own computer_use capture.
    supports_vision: bool = True
    # The turn belongs to Hermes: Jarvis's shortcuts and tool mandates stand
    # down (BrainManager._brain_orchestrates_tools).
    orchestrates_tools: bool = True
    # ``reasoning_effort="none"`` maps to Hermes's ``model_options`` opt-out.
    supports_thinking_switch: bool = True

    def __init__(self, model: str | None = None, base_url: str | None = None) -> None:
        del base_url  # re-read from config per call, so a card change applies at once
        self._model = (model or "").strip() or AGENT_MODEL
        self.last_runtime: dict[str, Any] | None = None
        # An ``httpx`` transport for tests (``httpx.MockTransport``); None = network.
        self.transport: Any = None
        # Housekeeping by default; BrainManager moves its conversation brain
        # onto SESSION_ID.
        self.session_id = BACKGROUND_SESSION_ID
        # Speaks a background result; None = Jarvis's announcement event.
        self.announce: Callable[[str, str], Awaitable[None]] | None = None
        self.delivery_poll_s = DELIVERY_POLL_S
        self._delegated = False

    def join_conversation(self) -> None:
        """Put this brain on the person's conversation session (voice and chat)."""
        self.session_id = SESSION_ID

    @property
    def pending_approval(self) -> _PendingApproval | None:
        """A Hermes run in this session parked on an approval the user was asked about."""
        return _session_state(self.session_id).pending_approval

    @pending_approval.setter
    def pending_approval(self, value: _PendingApproval | None) -> None:
        _session_state(self.session_id).pending_approval = value

    @property
    def _watcher(self) -> asyncio.Task[None] | None:
        return _session_state(self.session_id).watcher

    @_watcher.setter
    def _watcher(self, value: asyncio.Task[None] | None) -> None:
        _session_state(self.session_id).watcher = value

    def has_pending_confirmation(self) -> bool:
        """Shared across the voice and typed instances of this conversation."""
        return self.pending_approval is not None

    async def cancel_conversation(self) -> int:
        """Request native cancellation, including a run parked on approval.

        A failed request remains tracked for another Stop; never report that
        it stopped merely because its local event reader was cancelled.
        """
        state = _session_state(self.session_id)
        async with state.control_lock:
            count = 0
            for run in list(state.runs.values()):
                pending = state.pending_approval
                if await self._close(run, require_stop=True):
                    count += 1
                    if pending is not None and pending.run is run:
                        await self._post_approval(run, pending.request_id, "deny")
            # A completed foreground run may have left native children alive.
            # Discover them in Hermes, including after a Jarvis restart. The
            # watcher is retired only after native cancellation is accepted.
            count += await self._stop_delegations(state)
            if state.watcher is not None and not state.watcher.done():
                state.watcher.cancel()
                state.watcher = None
            return count

    async def _stop_delegations(self, state: _SessionState) -> int:
        base = configured_base_url()
        path = f"{base}/api/jarvis/conversations/{self.session_id}/delegations"
        try:
            async with self._client() as client:
                resp = await client.get(path, headers=self._headers(base))
                if resp.status_code != 200:
                    raise ValueError("Native delegation roster unavailable")
                payload = resp.json()
                roster = self._delegation_roster(payload)
                state.delegations = roster
                active = [
                    rid for rid, status in roster.items() if status in {"running", "stalling"}
                ]
                if not active:
                    return 0
                resp = await client.post(
                    path + "/stop", json={"delegation_ids": active}, headers=self._headers(base)
                )
                if resp.status_code != 200:
                    raise ValueError("Native delegation stop rejected")
                payload = resp.json()
                after = self._delegation_roster(payload)
                requested = payload.get("requested")
                if (payload.get("failed") != [] or not isinstance(requested, list)
                        or any(rid not in active for rid in requested)
                        or any(after.get(rid) not in {
                            "interrupt_requested", "completed", "interrupted", "error", "stalled",
                            "cancelled", "unknown", "finalizing",
                        } for rid in active)):
                    raise ValueError("Native delegation cancellation not confirmed")
                state.delegations = after
                return len(set(requested))
        except Exception as exc:  # noqa: BLE001 — never include provider bodies or credentials
            log.warning("Hermes background stop could not be confirmed (%s)", type(exc).__name__)
            raise RuntimeError(
                "Jarvis could not confirm background work's stop. Try Stop again. "
                "If this repeats, run free-voice setup and restart Hermes."
            ) from None

    def _delegation_roster(self, payload: Any) -> dict[str, str]:
        if not isinstance(payload, dict) or payload.get("session_id") != self.session_id:
            raise ValueError("Mismatched native conversation")
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise ValueError("Invalid native delegation roster")
        result = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("delegation_id"), str):
                raise ValueError("Missing native delegation id")
            rid, status = row["delegation_id"], row.get("status")
            if not rid or not isinstance(status, str) or rid in result:
                raise ValueError("Invalid native delegation state")
            result[rid] = status
        return result

    def can_call_tools(self) -> bool:
        return True

    def _headers(self, base_url: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "X-Hermes-Session-Key": SESSION_KEY}
        if not cfg.is_loopback_url(base_url):
            raise RuntimeError(
                f"Hermes Agent at {base_url} is not on this machine. Jarvis talks "
                "only to a Hermes API server on localhost."
            )
        key = read_api_server_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _client(self) -> Any:
        import httpx

        # Hermes sends a keepalive every 10 s, also while an approval waits.
        timeout = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)
        return httpx.AsyncClient(timeout=timeout, transport=self.transport)

    def request_body(self, req: BrainRequest) -> dict[str, Any]:
        """One run in the Jarvis session: Hermes loads the history itself."""
        body: dict[str, Any] = {
            **model_fields(self._model),
            "input": run_input(req),
            "instructions": build_instructions(req),
            "session_id": self.session_id,
        }
        if getattr(req, "reasoning_effort", None) == "none" or thinking_off_by_config():
            body["model_options"] = {"reasoning": {"enabled": False}}
        return body

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        # The runs API has no client-supplied read-only execution policy.
        # A prompt or tool.started event cannot prevent an already-started
        # terminal/MCP action. Refuse before starting any native run.
        if observation_only(latest_user_text(req)):
            if self.pending_approval is not None:
                await self.cancel_conversation()
            raise AgentPolicyError(
                "Hermes cannot enforce look-only access with this gateway. "
                "No new agent run was started."
            )
        pending, self.pending_approval = self.pending_approval, None
        if pending is not None:
            relay = await self._answer_pending(pending, latest_user_text(req))
            if relay is not None:
                async with aclosing(relay):
                    async for delta in relay:
                        yield delta
                return
        self.last_runtime = None
        self._delegated = False
        run = await self._start(self.request_body(req))
        # Closed with this turn, so a turn the user cuts off stops the run.
        async with aclosing(self._relay(run)) as relay:
            async for delta in relay:
                yield delta
        if self.last_runtime:
            log.info(
                "Hermes answered with %s/%s",
                self.last_runtime.get("provider"),
                self.last_runtime.get("model"),
            )

    async def _start(self, body: dict[str, Any]) -> _OpenRun:
        """Start one Hermes run and read its events on a background task.

        The reader outlives the turn only while the run waits for an approval
        the user was asked about.
        """
        async with _session_state(self.session_id).control_lock:
            return await self._start_unlocked(body)

    async def _start_unlocked(self, body: dict[str, Any]) -> _OpenRun:
        base_url = configured_base_url()
        headers = self._headers(base_url)
        async with self._client() as client:
            resp = await client.post(base_url + "/v1/runs", json=body, headers=headers)
        if resp.status_code not in (200, 202):
            if resp.status_code == 401:
                raise RuntimeError(
                    "Hermes Agent refused the request (401): the API server "
                    "key in Hermes's .env does not match."
                )
            raise RuntimeError(
                f"Hermes Agent answered HTTP {resp.status_code}. Check its local gateway."
            )
        run_id = str((resp.json() or {}).get("run_id") or "")
        if not run_id:
            raise RuntimeError("Hermes Agent started a run without a run id")
        run = _OpenRun(base_url=base_url, run_id=run_id, queue=asyncio.Queue())
        _session_state(self.session_id).runs[run_id] = run
        run.task = asyncio.create_task(self._pump(run), name="hermes-run-events")
        return run

    async def _pump(self, run: _OpenRun) -> None:
        url = f"{run.base_url}/v1/runs/{run.run_id}/events"
        try:
            async with (
                self._client() as client,
                client.stream("GET", url, headers=self._headers(run.base_url)) as resp,
            ):
                if resp.status_code != 200:
                    raise RuntimeError(f"Hermes run events answered HTTP {resp.status_code}")
                async for item in self._events(resp.aiter_lines()):
                    run.queue.put_nowait(item)
            run.queue.put_nowait(_END)
        except Exception as exc:  # noqa: BLE001 â€” handed to the reader, which raises it
            run.queue.put_nowait(exc)

    async def _relay(self, run: _OpenRun) -> AsyncIterator[BrainDelta]:
        """Deltas of one run until it ends or parks on an approval."""
        parked = False
        spoke = False
        try:
            while True:
                item = await run.queue.get()
                if run.stop_requested:
                    raise asyncio.CancelledError
                if item is _END:
                    if not run.finished:
                        raise RuntimeError("Hermes Agent disconnected before completing this task.")
                    break
                if isinstance(item, BaseException):
                    raise item
                name = str(item.get("event") or "") if isinstance(item, dict) else ""
                if name == "approval.request":
                    parked = True
                    language = phrase_language()
                    self.pending_approval = _PendingApproval(
                        run=run, request_id=str(item.get("request_id") or ""), language=language
                    )
                    yield BrainDelta(content=approval_question(item, language))
                    yield BrainDelta(finish_reason="stop")
                    return
                if name in {"tool.started", "tool.completed"}:
                    await self._tool_activity(run, name, item)
                if name in _FINISHED:
                    run.finished = True
                    if name != "run.completed" or (
                        item.get("completed") is False
                        or item.get("partial")
                        or item.get("failed")
                        or item.get("error")
                    ):
                        from jarvis.brain.provider_test import classify_provider_error

                        category = classify_provider_error(str(item.get("error") or ""))
                        safe_reason = {
                            "rate_limited": "rate limited (429)",
                            "no_credits": "out of credit or quota",
                            "bad_key": "authentication refused (401)",
                            "missing_key": "provider is not connected",
                        }.get(category, "task incomplete")
                        raise RuntimeError(
                            f"Hermes Agent did not complete this task: {safe_reason}."
                        )
                    output = item.get("output")
                    if not spoke and isinstance(output, str) and output.strip():
                        yield BrainDelta(content=output)
                for delta in self._deltas_for(name, item):
                    spoke = spoke or bool(delta.content)
                    yield delta
        finally:
            if not parked:
                try:
                    for tool, call_id in list(run.open_tools):
                        await self._publish_tool(run, tool, call_id, "interrupted",
                                                 "No tool completion was reported.")
                    run.open_tools.clear()
                finally:
                    await self._close(run)
        if self._delegated:
            self._watch_delivery()

    async def _publish_tool(
        self, run: _OpenRun, tool: str, call_id: str, state: Any,
        preview: str, duration_ms: int = 0,
    ) -> None:
        from jarvis.core.agent_turn import current_agent_turn
        from jarvis.core.events import AgentToolActivity
        from jarvis.core.redact import safe_preview

        turn = current_agent_turn.get()
        if turn is not None and turn.publish is not None:
            await turn.publish(AgentToolActivity(
                trace_id=turn.trace_id, source_layer="brain.hermes",
                run_id=run.run_id, call_id=call_id, tool_name=f"hermes:{tool}",
                state=state, preview=safe_preview(preview, max_chars=2000),
                duration_ms=duration_ms,
            ))

    async def _tool_activity(self, run: _OpenRun, name: str, item: dict[str, Any]) -> None:
        tool = tool_name_of(item) or "tool"
        if name == "tool.started":
            call_id = uuid4().hex
            run.open_tools.append((tool, call_id))
            await self._publish_tool(run, tool, call_id, "started", str(item.get("preview") or ""))
            return
        # Hermes currently supplies tool names, not per-call IDs. Pair the
        # native stream's oldest matching start, independently for each run.
        index = next((i for i, (name, _) in enumerate(run.open_tools) if name == tool), None)
        call_id = run.open_tools.pop(index)[1] if index is not None else uuid4().hex
        try:
            duration = float(item.get("duration") or 0)
            duration_ms = int(max(0, duration) * 1000) if math.isfinite(duration) else 0
        except (ValueError, TypeError, OverflowError):
            duration_ms = 0
        success = item.get("error") is False
        preview = str(item.get("preview") or ("Completed." if success else "Tool did not succeed."))
        await self._publish_tool(run, tool, call_id, "completed" if success else "failed",
                                 preview, duration_ms)

    async def _close(self, run: _OpenRun, *, require_stop: bool = False) -> bool:
        """Retire a completed run or request its native stop, once per run."""
        async with run.close_lock:
            requested = False
            if not run.finished and not run.stop_requested:
                try:
                    async with self._client() as client:
                        resp = await client.post(
                            f"{run.base_url}/v1/runs/{run.run_id}/stop",
                            headers=self._headers(run.base_url),
                        )
                    if resp.status_code not in (200, 202) or resp.json().get("status") not in {
                        "stopping", "stopped", "completed", "failed", "cancelled", "interrupted",
                    }:
                        raise RuntimeError("Native stop was not accepted")
                    run.stop_requested = True
                    requested = True
                    # Wake an active relay as well as retiring a parked one.
                    run.queue.put_nowait(asyncio.CancelledError())
                except Exception as exc:  # noqa: BLE001 â€” no raw provider body or key
                    log.warning("Hermes run stop could not be confirmed (%s)", type(exc).__name__)
                    if require_stop:
                        raise RuntimeError(
                            "Jarvis could not confirm the agent's stop. Try Stop again."
                        ) from None
            if run.finished or run.stop_requested:
                state = _session_state(self.session_id)
                state.runs.pop(run.run_id, None)
                if state.pending_approval is not None and state.pending_approval.run is run:
                    state.pending_approval = None
            if run.task is not None:
                run.task.cancel()
            return requested

    async def _answer_pending(
        self, pending: _PendingApproval, text: str
    ) -> AsyncIterator[BrainDelta] | None:
        """Resolve a parked approval with the user's answer.

        Returns the rest of the same Hermes run to relay, or ``None`` when the
        answer was not about it and the turn is a new run.
        """
        from jarvis.voice.echo_confirmation import classify_response
        from jarvis.voice.tool_confirmation import format_confirm_outcome

        verdict = classify_response(text, language=pending.language)
        if verdict == "ambiguous" and pending.reasks < MAX_APPROVAL_REASKS:
            pending.reasks += 1
            self.pending_approval = pending
            return _single(format_confirm_outcome("unclear", "hermes", language=pending.language))
        choice = "once" if verdict == "confirm" else "deny"
        resolved = await self._post_approval(pending.run, pending.request_id, choice)
        if resolved and verdict != "unknown":
            return self._relay(pending.run)
        # The user moved on, or the run had already ended: let Hermes finish it
        # with the denial before the new turn takes the session.
        await self._drain(pending.run)
        return None

    async def _post_approval(self, run: _OpenRun, request_id: str, choice: str) -> bool:
        body: dict[str, Any] = {"choice": choice}
        if request_id:
            body["request_id"] = request_id
        url = f"{run.base_url}/v1/runs/{run.run_id}/approval"
        try:
            async with self._client() as client:
                resp = await client.post(url, json=body, headers=self._headers(run.base_url))
        except Exception as exc:  # noqa: BLE001 â€” an unreachable Hermes counts as unresolved
            log.warning("Hermes approval %s could not be sent: %s", choice, exc)
            return False
        if resp.status_code != 200:
            log.info("Hermes approval %s not taken (HTTP %s)", choice, resp.status_code)
            return False
        log.info("Hermes approval resolved: %s", choice)
        return True

    async def _drain(self, run: _OpenRun) -> None:
        """Read an abandoned run to its end, denying any further approval."""
        try:
            while True:
                item = await asyncio.wait_for(run.queue.get(), DRAIN_WAIT_S)
                if item is _END or isinstance(item, BaseException):
                    return
                name = item.get("event") if isinstance(item, dict) else None
                if name in _FINISHED:
                    run.finished = True
                if name == "approval.request":
                    await self._post_approval(run, str(item.get("request_id") or ""), "deny")
        except TimeoutError:
            log.info("Hermes run %s still busy after the user moved on", run.run_id)
        finally:
            await self._close(run)

    # --- background delegation results ---------------------------------

    def _watch_delivery(self) -> None:
        if self._watcher is not None and not self._watcher.done():
            return
        self._watcher = asyncio.create_task(self._deliver(), name="hermes-delegation-result")

    async def _delivery_ids(self) -> set[str] | None:
        base_url = configured_base_url()
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"{base_url}/api/sessions/{self.session_id}/messages",
                    params={"order": "latest", "limit": "50"},
                    headers=self._headers(base_url),
                )
            rows = resp.json().get("data") if resp.status_code == 200 else None
        except Exception as exc:  # noqa: BLE001 â€” the watcher tries again on its next tick
            log.debug("Hermes session read failed: %s", exc)
            return None
        return {
            str(row.get("id"))
            for row in rows or ()
            if isinstance(row, dict) and row.get("display_kind") == DELIVERY_KIND
        }

    async def _deliver(self) -> None:
        """Wait for a background result in the session, then speak Hermes's account of it."""
        baseline = await self._delivery_ids() or set()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + DELIVERY_WAIT_S
        while loop.time() < deadline:
            await asyncio.sleep(self.delivery_poll_s)
            # An open approval question owns the user's next words.
            if self.pending_approval is not None:
                continue
            ids = await self._delivery_ids()
            if ids is None or not (ids - baseline):
                continue
            req = BrainRequest(messages=(BrainMessage(role="user", content=DELIVERY_PROMPT),))
            text = ""
            async for delta in self.complete(req):
                text += delta.content or ""
            if text.strip():
                await self._announce(text.strip())
            return
        log.info("No background result from Hermes within %.0f s", DELIVERY_WAIT_S)

    async def _announce(self, text: str) -> None:
        language = phrase_language()
        if self.announce is not None:
            await self.announce(text, language)
            return
        from jarvis.core.events import AnnouncementRequested
        from jarvis.core.runtime_refs import get_brain_manager

        bus = getattr(get_brain_manager(), "_bus", None)
        if bus is None:
            log.warning("Hermes background result could not be spoken: no event bus")
            return
        result = bus.publish(
            AnnouncementRequested(
                source_layer="brain.hermes",
                text=text,
                language=language,
                kind="subagent",
            )
        )
        if asyncio.iscoroutine(result):
            await result

    async def _events(self, lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
        """Run events (``data:`` JSON carrying ``event``); keepalives as ``{}``."""
        data: list[str] = []
        async for raw in lines:
            line = raw.rstrip("\r")
            if line.startswith(":"):
                yield {}
                continue
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
                continue
            if line or not data:
                continue
            payload_text, data = "\n".join(data), []
            try:
                payload = json.loads(payload_text)
            except ValueError:
                log.debug("Hermes run stream: skipped a non-JSON event")
                continue
            if isinstance(payload, dict):
                yield payload

    def _deltas_for(self, name: str, item: dict[str, Any]) -> list[BrainDelta]:
        if name == "message.delta":
            text = item.get("delta")
            return [BrainDelta(content=text if isinstance(text, str) and text else None)]
        if name == "tool.completed" and item.get("error") is False:
            tool = tool_name_of(item)
            if tool == DELEGATE_TOOL:
                self._delegated = True
            return [BrainDelta(agent_tools=(f"hermes:{tool or 'tool'}",))]
        if name == "run.completed":
            runtime = item.get("runtime")
            if isinstance(runtime, dict):
                self.last_runtime = runtime
            out = [BrainDelta(finish_reason="stop")]
            usage = item.get("usage")
            if isinstance(usage, dict):
                out.append(
                    BrainDelta(
                        usage={
                            "input_tokens": int(usage.get("input_tokens") or 0),
                            "output_tokens": int(usage.get("output_tokens") or 0),
                        }
                    )
                )
            return out
        # A keepalive, tool completion, subagent or reasoning event: an empty
        # delta tells the caller's no-progress watchdog that Hermes is working.
        return [BrainDelta()]

    def estimate_cost(self, req: BrainRequest) -> float:
        # Hermes bills (or not) on its own providers; Jarvis cannot see the
        # price of the model Hermes picks, so it books nothing here.
        del req
        return 0.0
