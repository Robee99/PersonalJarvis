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
reports starting (``tool.started`` events) are passed on as
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
import contextvars
import json
import logging
import os
import re
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.core import config as cfg
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
class Checkpoint:
    """What a turn had done when it was cut off: enough to pick it up again."""

    request: str
    said: str = ""
    tools: list[str] = field(default_factory=list)
    delegated: bool = False
    interrupted_at: float = 0.0


@dataclass(slots=True)
class StopReport:
    """What one spoken "stop" actually stopped in a Hermes session."""

    answer: bool = False
    approval: bool = False
    background: bool = False

    @property
    def count(self) -> int:
        return int(self.answer) + int(self.approval) + int(self.background)


@dataclass(slots=True)
class _SessionState:
    """What belongs to a Hermes session, not to one HermesBrain object.

    Voice and the typed chat build separate brain objects for the same
    session; an approval asked in one is answered in the other, one
    background result is announced once, and a "stop" said in one stops the
    run the other started.
    """

    pending_approval: _PendingApproval | None = None
    watcher: asyncio.Task[None] | None = None
    #: The run a turn is reading right now (its ``progress`` is what it has done).
    active: _OpenRun | None = None
    #: The last turn the user cut off, until "continue" takes it or it expires.
    checkpoint: Checkpoint | None = None


_SESSIONS: dict[str, _SessionState] = {}


def _session_state(session_id: str) -> _SessionState:
    return _SESSIONS.setdefault(session_id, _SessionState())


#: Every run Jarvis started that may still be going: run id -> (base URL,
#: session, the open run when this process reads it). Hermes keeps a run going
#: after its reader is gone, and it runs two runs of one session side by side,
#: so a run nobody reads anymore must be stopped by id (2026-10-12: a shopping
#: task kept driving the PC for 15 minutes after the app was closed, reopened
#: and a new chat started, and "stop" only reached the newer run).
_LIVE_RUNS: dict[str, tuple[str, str, _OpenRun | None]] = {}
#: Where the live runs are kept for the next Jarvis process; None = not on disk.
LIVE_RUNS_FILE = "hermes-live-runs.json"
_ORPHAN_SWEEP: asyncio.Task[int] | None = None


def live_runs_path() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / LIVE_RUNS_FILE


def read_live_runs(path: Path) -> dict[str, tuple[str, str]]:
    """The runs a Jarvis process left on disk (run id -> base URL, session)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    runs: dict[str, tuple[str, str]] = {}
    for run_id, entry in data.items():
        if isinstance(entry, dict) and isinstance(entry.get("base_url"), str):
            runs[str(run_id)] = (entry["base_url"], str(entry.get("session_id") or ""))
    return runs


def write_live_runs(path: Path) -> None:
    runs = {
        run_id: {"base_url": base_url, "session_id": session_id}
        for run_id, (base_url, session_id, _run) in _LIVE_RUNS.items()
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(runs), encoding="utf-8")
    except OSError as exc:
        log.debug("Could not save Hermes live runs: %s", exc)


def take_checkpoint(session_id: str = "jarvis-main") -> Checkpoint | None:
    """The stopped task "continue" should pick up, once; ``None`` when none is fresh."""
    state = _session_state(session_id)
    checkpoint, state.checkpoint = state.checkpoint, None
    if checkpoint is None or time.monotonic() - checkpoint.interrupted_at > CHECKPOINT_TTL_S:
        return None
    return checkpoint


def resume_input(checkpoint: Checkpoint) -> str:
    """The run input that picks a stopped task back up where it was cut off."""
    parts = [f"Continue the task I stopped a moment ago. My request was: {checkpoint.request!r}."]
    said = checkpoint.said.strip()
    if said:
        parts.append(f"Before I stopped you, you had already said: {said[-600:]!r}.")
    if checkpoint.tools:
        parts.append("Tools you had already started: " + ", ".join(checkpoint.tools[-8:]) + ".")
    parts.append(
        "Pick up where you left off and finish it. Do not repeat steps that "
        "already ran, and do not repeat what you already said."
    )
    return " ".join(parts)


def _bus() -> Any:
    """Jarvis's event bus, or ``None`` outside the running app (tests, CLI)."""
    from jarvis.core.runtime_refs import get_brain_manager

    return getattr(get_brain_manager(), "_bus", None)


#: Event publishes in flight, kept so the loop does not drop them.
_PUBLISHING: set[asyncio.Task[Any]] = set()


def publish_event(event: Any) -> None:
    """Put ``event`` on Jarvis's bus without waiting for its subscribers."""
    bus = _bus()
    if bus is None:
        return
    try:
        result = bus.publish(event)
        if asyncio.iscoroutine(result):
            task = asyncio.get_running_loop().create_task(result, name="hermes-event")
            _PUBLISHING.add(task)
            task.add_done_callback(_PUBLISHING.discard)
    except RuntimeError as exc:  # no running loop: a sync caller; the event is advisory
        log.debug("Hermes event %s not published: %s", type(event).__name__, exc)


def publish_state(state: str, detail: str = "", session_id: str = "") -> None:
    """Put the turn's state on Jarvis's event bus; no bus (tests, CLI) is a no-op."""
    from jarvis.core.events import ConversationStateChanged

    publish_event(
        ConversationStateChanged(
            source_layer="brain.hermes", state=state, detail=detail, session_id=session_id
        )
    )


def turn_context() -> dict[str, Any]:
    """The typed chat's tool context for this turn (``delivery``,
    ``approval_surface``, ``approval_ref``); empty for a spoken turn."""
    manager = sys.modules.get("jarvis.brain.manager")
    var = getattr(manager, "_TURN_OVERRIDE", None)
    override = var.get() if var is not None else None
    context = getattr(override, "tool_context", None)
    return dict(context) if isinstance(context, dict) else {}


def _background(coro: Any, name: str) -> asyncio.Task[Any]:
    """A task that outlives the turn, without the turn's chat context (a
    background result is spoken, and its approvals are not that chat's)."""
    return asyncio.get_running_loop().create_task(coro, name=name, context=contextvars.Context())

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

#: How long a run handed off with ``finish_late`` may still take before Jarvis stops it.
LATE_ANSWER_MAX_S = 300.0

#: Said when a run Jarvis let finish late ends without an answer.
LATE_FAILED_MESSAGE = "The answer I was still working on didn't come through. Please ask again."

#: How long a chat's approval card waits before the call is denied.
CARD_TIMEOUT_S = 300.0
#: How long a stopped task can still be picked up with "continue".
CHECKPOINT_TTL_S = 1800.0
#: A turn cut off this recently was stopped by the "stop" that follows it
#: (barge-in closes the stream first, then "stop" arrives as its own turn).
RECENT_INTERRUPT_S = 20.0
#: Asked in the session when a "stop" lands while delegated work still runs:
#: only Hermes can stop its own subagents (``delegate_task`` action=stop).
STOP_DELEGATIONS_PROMPT = (
    "The user said stop. Stop every background subagent you started in this "
    "conversation now (delegate_task with action list, then action stop for "
    "each one). Do not start anything new. Reply with one short sentence."
)
#: Unclear answers to one approval question before Jarvis denies it.
MAX_APPROVAL_REASKS = 2
#: Wait for the rest of a Hermes run the user moved away from.
DRAIN_WAIT_S = 30.0
_DESTRUCTIVE_WORDS = re.compile(r"delet|remov|\brm\b|wipe|format|erase|drop", re.IGNORECASE)

#: Spoken-front-end contract layered on top of Hermes's own system prompt.
#: How JARVIS talks, for spoken and typed turns alike. Taken from the owner's
#: own JARVIS prototype (2026-10-11): a direct, competent peer, not a chatbot.
PERSONA_INSTRUCTIONS = (
    "Talk like a capable colleague: direct, calm, quietly confident. Start with "
    "the answer, never with filler such as \"Sure!\" or \"Great question\". "
    "No \"sir\", no disclaimers about being an AI, no film references. Match "
    "the user's energy: a short command gets a short reply, and \"Done.\" is a "
    "full answer when a task is done. For an error say what happened and what "
    "to do next. If you don't know, say so. Dry wit only rarely; a useful "
    "suggestion the user didn't ask for gets one line at most."
)

FRONT_END_INSTRUCTIONS = (
    "You are answering through JARVIS, the user's voice and desktop front-end. "
    "Each user message starts with a short note in square brackets. "
    "[spoken]: the reply is spoken aloud, so lead with the answer, in one to "
    "three short sentences of plain text, no markdown, no lists, unless the "
    "user asks for detail. [typed]: the reply is read in the chat window, so "
    "lead with the answer; short markdown (lists, code blocks, links) is fine "
    "when it helps. Follow any reply-language or look-only rule in that note. "
    "Use your own tools to act on the computer, the browser and files. Say you "
    "did something only after a tool actually did it."
)

#: How to drive the Windows PC so the person sees the result. Live 2026-10-12:
#: a bare ``Start-Process chrome.exe`` from the background gateway opened
#: Chrome behind the Jarvis window, the model said "I have opened Chrome",
#: and the person, seeing nothing, rightly called it a lie.
PC_CONTROL_INSTRUCTIONS = (
    "On this Windows PC, launch apps with the windows App tool. To minimize, "
    "maximize, restore, bring back, focus or close a window use "
    "mcp__jarvis__window_control; for the computer's volume or mute use "
    "mcp__jarvis__system_volume (never a music app). Resizing is not "
    "maximizing. Use PowerShell only for what these tools cannot do. Before "
    "saying something is open, closed, "
    "playing, added or sent, check the result (a Snapshot, the window list, "
    "the page) and say what you actually see. Never say a cart, order, "
    "message or file changed unless a tool result in this turn shows it."
)

#: Added when the user asked only to look ("don't click anything, just look").
OBSERVE_ONLY_INSTRUCTIONS = (
    "This turn is observation only: the user asked you to look, not to act. "
    "You may capture or read the screen, a page or a file, but do not click, "
    "type, press keys, open, close, launch, send, write or change anything. "
    "Describe what you see."
)

#: Tools that change something; one starting in an observation-only turn
#: stops the run. ``computer_use`` input actions ask for approval first, and
#: Jarvis denies those itself in such a turn.
_ACTING_TOOLS = frozenset({
    "browser_click", "browser_type", "browser_press", "browser_vault_fill",
    "browser_vault_save_login", "write_file", "patch",
})


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
    except Exception:  # noqa: BLE001 — an unreadable config keeps Hermes's own reasoning setting
        return False
    return provider is not None and getattr(provider, "thinking_budget", None) == 0


def configured_base_url() -> str:
    """The card's server URL, else Hermes's default API server address."""
    try:
        provider = cfg.load_config().brain.providers.get("hermes")
    except Exception:  # noqa: BLE001 — an unreadable config falls back to the default address
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


#: Levels the composer's thinking picker sends (``agent_chat.effort`` "hermes").
THINKING_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh", "max"})
#: Hermes providers that are llama.cpp servers on this machine (``local-qwen``,
#: ``local-gemma-2``): Hermes sends them no reasoning setting at all.
LOCAL_PROVIDER_PREFIX = "local-"
#: ``qwen@high``: the model swapper in front of the local servers turns the
#: suffix into llama.cpp's per-request ``enable_thinking`` and thinking budget.
THINKING_SEPARATOR = "@"


def local_thinking_model(model: str, effort: str) -> str:
    """The model name that carries a thinking level to a local llama.cpp server."""
    base = model.split(THINKING_SEPARATOR, 1)[0]
    return f"{base}{THINKING_SEPARATOR}{effort}" if base else model


def build_instructions(req: BrainRequest) -> str:
    """The front-end contract layered on Hermes's own system prompt.

    Jarvis's own router prompt (its tool rules and tool names) is not sent:
    Hermes has its own tools and would chase names it does not have. The
    user's standing preferences are kept.

    The text is the same on every turn. Hermes puts it in the system prompt,
    ahead of the tools and the history, and llama.cpp reuses its cache only
    up to the first changed token: a per-turn variant (spoken vs typed, the
    reply language, look-only) made Gemma re-read the whole ~47k-token prompt
    whenever the user switched between voice and chat, about a minute each
    time (2026-10-12). What changes per turn goes in ``turn_note`` instead.
    """
    parts = [FRONT_END_INSTRUCTIONS, PC_CONTROL_INSTRUCTIONS, PERSONA_INSTRUCTIONS]
    prefs = render_cli_standing_instructions(req.system)
    if prefs:
        parts.append(prefs)
    return "\n\n".join(parts)


#: Added to the first message of a new typed chat. The chat shares the one
#: Hermes session with voice, so without it the model picked an unfinished
#: task from the history back up in a chat the user had just opened.
NEW_CHAT_NOTE = (
    "New chat: tasks from earlier messages are over. Do not continue or retry "
    "them unless this message asks for it."
)


#: Added to every other turn. All of Jarvis shares one Hermes session, and a
#: small local model answered "show me the earthquakes on the globe" with the
#: Drive task from three turns earlier (2026-10-20). A follow-up such as "yes"
#: or "carry on" still continues, because it asks for exactly that.
FOCUS_NOTE = "Act on this message. An earlier task is over unless this message continues it."


def is_first_chat_message(req: BrainRequest) -> bool:
    """Whether the newest message is the first the user wrote in this chat."""
    return sum(1 for m in req.messages if getattr(m, "role", None) == "user") == 1


def turn_note(req: BrainRequest, *, written: bool = False) -> str:
    """The per-turn part of the contract, sent at the head of the user message."""
    parts = ["[typed]" if written else "[spoken]"]
    if written and is_first_chat_message(req):
        parts.append(NEW_CHAT_NOTE)
    else:
        parts.append(FOCUS_NOTE)
    lang = extract_reply_language_directive(req.system)
    if lang:
        parts.append(lang)
    if observation_only(latest_user_text(req)):
        parts.append(OBSERVE_ONLY_INSTRUCTIONS)
    return " ".join(parts)


def observation_only(text: str) -> bool:
    """Whether the user asked only to look (Jarvis's own look-not-act gate)."""
    from jarvis.brain.cu_gate import is_observation_only

    return bool(text) and is_observation_only(text)


def latest_user_message(req: BrainRequest) -> BrainMessage | None:
    for message in reversed(req.messages):
        if getattr(message, "role", None) == "user":
            return message
    return None


def run_input(req: BrainRequest, *, written: bool = False) -> str | list[dict[str, Any]]:
    """The run's ``input``: the turn note, the newest user text, and its images.

    Images go as OpenAI ``image_url`` parts in one user message; Hermes hands
    them to a model that can see, or describes them with its own vision tool
    when the model cannot. Whether an image may leave the machine at all is
    decided before this brain is picked (``route_policy.media_chain``).
    """
    text = latest_user_text(req)
    note = turn_note(req, written=written)
    message = latest_user_message(req)
    images = tuple(getattr(message, "images", ()) or ()) if message is not None else ()
    if not images:
        return f"{note}\n{text}"
    parts: list[dict[str, Any]] = [{"type": "text", "text": f"{note}\n{text}"}]
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
    except Exception:  # noqa: BLE001 — an unreadable config speaks the default language
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


#: Per-turn context BrainManager puts before the user's words.
_TURN_CONTEXT = re.compile(r"^\[Current date and time:[^\]]*\]\s*")


def spoken_text(text: str) -> str:
    """The user's own words, without the per-turn context Jarvis prepends."""
    return _TURN_CONTEXT.sub("", text or "", count=1).strip()


def is_resume_request(text: str) -> bool:
    """Whether the user said nothing but "continue" (any locale)."""
    from jarvis.speech.interrupt_intent import is_resume_intent

    words = spoken_text(text)
    # Further context blocks (memory, focus) end before the user's own words.
    return is_resume_intent(words.rsplit("\n\n", 1)[-1]) if words else False


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
    closed: bool = False
    #: Stopped by a spoken "stop": nothing of it is watched for or announced.
    cancelled: bool = False
    #: What the turn reading this run has done so far; ``None`` for a run
    #: Jarvis stopped itself (nothing to "continue").
    progress: Checkpoint | None = None
    #: The turn gave up waiting but the run goes on; its answer is spoken
    #: when it lands (``finish_late``).
    late: bool = False
    late_reader: bool = False


@dataclass
class _PendingApproval:
    """A Hermes run parked on an approval the user was asked about."""

    run: _OpenRun
    request_id: str
    language: str
    reasks: int = field(default=0)


def finish_late(session_id: str = SESSION_ID) -> bool:
    """Let the run a voice turn gave up on finish, and speak its answer then.

    Live 2026-10-12 02:51: "Hallo" arrived when the session was due for
    compaction. Hermes spent ~90 s summarising the history before the model
    saw the turn; Jarvis's 90 s voice ceiling stopped the run, so the
    compaction was thrown away, and the next turn started it again. Stopping a
    run that is still working only loses its work, so the turn ends but the
    run goes on and its answer is announced when it lands.

    True when a run was handed off: a run of the session that is still going
    and has not said anything yet (a run that already spoke keeps the old
    ending, since repeating it would talk over what was heard).
    """
    state = _SESSIONS.get(session_id)
    run = state.active if state is not None else None
    if run is None or run.finished or run.closed or run.cancelled:
        return False
    if run.progress is None or run.progress.said:
        return False
    run.late = True
    return True


def observe_only_stop_message(tool: str) -> str:
    """What Jarvis says when Hermes tried to act in a look-only turn."""
    return (
        f"I stopped Hermes: you asked me only to look, and it started to act ({tool})."
    )


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
        self._observe_only = False
        #: The chat's approval card for this turn ("" = ask by voice).
        self._card_ref = ""
        self._card_timeout_s = CARD_TIMEOUT_S

    def join_conversation(self) -> None:
        """Put this brain on the person's conversation session (voice and chat).

        Also stops what an earlier Jarvis process left running, as soon as
        there is a loop to do it on.
        """
        self.session_id = SESSION_ID
        global _ORPHAN_SWEEP
        if _ORPHAN_SWEEP is None and self._persists():
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return  # no loop yet: the first turn sweeps
            _ORPHAN_SWEEP = _background(self.stop_orphaned_runs(), "hermes-orphan-sweep")

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
            "input": run_input(req, written=turn_context().get("delivery") == "written"),
            "instructions": build_instructions(req),
            "session_id": self.session_id,
        }
        effort = str(getattr(req, "reasoning_effort", None) or "").strip().lower()
        if effort == "none" or (not effort and thinking_off_by_config()):
            body["model_options"] = {"reasoning": {"enabled": False}}
        elif effort in THINKING_EFFORTS:
            body["model_options"] = {"reasoning": {"enabled": True, "effort": effort}}
            if str(body.get("provider") or "").startswith(LOCAL_PROVIDER_PREFIX):
                body["model"] = local_thinking_model(str(body.get("model") or ""), effort)
        return body

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        pending, self.pending_approval = self.pending_approval, None
        if pending is not None:
            relay = await self._answer_pending(pending, latest_user_text(req))
            if relay is not None:
                async with aclosing(relay):
                    async for delta in relay:
                        yield delta
                return
        late = _session_state(self.session_id).active
        if late is not None and late.late and not late.finished:
            # A new turn supersedes the answer still on its way.
            late.cancelled = True
            await self._close(late)
        if self.session_id == SESSION_ID:
            await self._sweep_orphans_once()
            await self._stop_other_runs()
        self.last_runtime = None
        self._delegated = False
        context = turn_context()
        self._card_ref = (
            str(context.get("approval_ref") or "")
            if context.get("approval_surface") == "interactive"
            else ""
        )
        self._card_timeout_s = float(context.get("approval_timeout_s") or CARD_TIMEOUT_S)
        text = latest_user_text(req)
        self._observe_only = observation_only(text)
        body = self.request_body(req)
        resumed = take_checkpoint(self.session_id) if is_resume_request(text) else None
        if resumed is not None:
            body["input"] = resume_input(resumed)
            progress = Checkpoint(
                request=resumed.request, said=resumed.said, tools=list(resumed.tools)
            )
            publish_state("RESUMED", session_id=self.session_id)
        else:
            progress = Checkpoint(request=spoken_text(text))
            publish_state("THINKING", session_id=self.session_id)
        run = await self._start(body)
        run.progress = progress
        _session_state(self.session_id).active = run
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
        self._track(run_id, base_url, run)
        run.task = asyncio.create_task(self._pump(run), name="hermes-run-events")
        return run

    # --- runs nobody reads anymore ----------------------------------------

    def _persists(self) -> bool:
        # Only a real Hermes on this machine; tests use a mock transport.
        return self.transport is None

    def _track(self, run_id: str, base_url: str, run: _OpenRun | None) -> None:
        _LIVE_RUNS[run_id] = (base_url, self.session_id, run)
        if self._persists():
            write_live_runs(live_runs_path())

    def _untrack(self, run_id: str) -> None:
        if _LIVE_RUNS.pop(run_id, None) is not None and self._persists():
            write_live_runs(live_runs_path())

    async def _post_stop(self, base_url: str, run_id: str) -> None:
        try:
            async with self._client() as client:
                await client.post(
                    f"{base_url}/v1/runs/{run_id}/stop", headers=self._headers(base_url)
                )
        except Exception as exc:  # noqa: BLE001 — Hermes ends the run on its own timeout
            log.warning("Hermes run %s could not be stopped: %s", run_id, exc)

    async def stop_orphaned_runs(self) -> int:
        """Stop the runs an earlier Jarvis process started and never saw end.

        Closing the app drops the event stream, not the run: Hermes carried a
        task on, using the PC, after Jarvis was closed and reopened.
        """
        if not self._persists():
            return 0
        left = {
            run_id: entry
            for run_id, entry in read_live_runs(live_runs_path()).items()
            if run_id not in _LIVE_RUNS
        }
        for run_id, (base_url, _session) in left.items():
            if cfg.is_loopback_url(base_url):
                await self._post_stop(base_url, run_id)
                log.info("Stopped Hermes run %s left by an earlier Jarvis", run_id)
        write_live_runs(live_runs_path())
        return len(left)

    async def _sweep_orphans_once(self) -> None:
        global _ORPHAN_SWEEP
        if not self._persists():
            return
        if _ORPHAN_SWEEP is None:
            _ORPHAN_SWEEP = _background(self.stop_orphaned_runs(), "hermes-orphan-sweep")
        try:
            await asyncio.shield(_ORPHAN_SWEEP)
        except Exception as exc:  # noqa: BLE001 — a failed sweep never blocks a turn
            log.warning("Sweeping old Hermes runs failed: %s", exc)

    async def _stop_other_runs(self, keep: _OpenRun | None = None) -> int:
        """Stop every run of this conversation still going, but ``keep``.

        A new turn supersedes them: Hermes would otherwise run them side by
        side in one history, and a "stop" reached only the newest.
        """
        stopped = 0
        for run_id, (base_url, session_id, run) in list(_LIVE_RUNS.items()):
            if session_id != self.session_id or (keep is not None and run is keep):
                continue
            if run is not None:
                if run.finished or run.closed:
                    _LIVE_RUNS.pop(run_id, None)
                    continue
                run.cancelled = True
                run.late = False
                await self._close(run)
            else:
                await self._post_stop(base_url, run_id)
                self._untrack(run_id)
            stopped += 1
        if stopped:
            log.info("Stopped %d earlier Hermes run(s) of %s", stopped, self.session_id)
        return stopped

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
            self._untrack(run.run_id)
            run.queue.put_nowait(_END)
        except Exception as exc:  # noqa: BLE001 — handed to the reader, which raises it
            run.queue.put_nowait(exc)

    async def _relay(self, run: _OpenRun) -> AsyncIterator[BrainDelta]:
        """Deltas of one run until it ends or parks on an approval."""
        parked = False
        spoke = False
        try:
            while True:
                item = await run.queue.get()
                if item is _END:
                    if not run.finished and not run.closed:
                        raise RuntimeError("Hermes Agent disconnected before completing this task.")
                    break
                if isinstance(item, BaseException):
                    raise item
                name = str(item.get("event") or "") if isinstance(item, dict) else ""
                if name == "approval.request" and self._observe_only:
                    # The user asked only to look: nothing that needs approval runs.
                    await self._post_approval(run, str(item.get("request_id") or ""), "deny")
                    log.info("Hermes approval denied: the user asked only to look")
                    continue
                if name == "tool.started" and self._observe_only:
                    tool = tool_name_of(item)
                    if tool in _ACTING_TOOLS:
                        log.warning("Hermes started %s in a look-only turn; stopping it", tool)
                        # Jarvis stopped it, not the user: nothing to continue.
                        run.progress = None
                        yield BrainDelta(content=observe_only_stop_message(tool))
                        yield BrainDelta(finish_reason="stop")
                        return
                if name == "approval.request" and self._card_ref:
                    # A typed turn: the chat's approval card answers, and the
                    # same run carries on in the same turn.
                    choice = await self._ask_card(item)
                    request_id = str(item.get("request_id") or "")
                    if await self._post_approval(run, request_id, choice):
                        continue
                if name == "approval.request":
                    parked = True
                    language = phrase_language()
                    self.pending_approval = _PendingApproval(
                        run=run, request_id=str(item.get("request_id") or ""), language=language
                    )
                    yield BrainDelta(content=approval_question(item, language))
                    yield BrainDelta(finish_reason="stop")
                    return
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
                for delta in self._deltas_for(name, item, run):
                    spoke = spoke or bool(delta.content)
                    yield delta
        finally:
            if not parked:
                await self._close(run)
        if self._delegated and not run.cancelled:
            self._watch_delivery()

    async def _close(self, run: _OpenRun) -> None:
        """Stop a run the user cut off; dropping its event stream alone would not.

        What the turn had done is kept, so "continue" can pick it up.
        """
        if run.closed:
            return
        if run.late and not run.finished and not run.cancelled and run.task is not None:
            # The turn ends here; the run does not (``finish_late``).
            if not run.late_reader:
                run.late_reader = True
                _background(self._finish_late(run), "hermes-late-answer")
            return
        run.closed = True
        state = _session_state(self.session_id)
        if state.active is run:
            state.active = None
        progress, run.progress = run.progress, None
        if run.finished:
            if progress is not None:
                publish_state("COMPLETED", session_id=self.session_id)
        elif progress is not None:
            progress.interrupted_at = time.monotonic()
            state.checkpoint = progress
            publish_state("INTERRUPTED", session_id=self.session_id)
        if not run.finished:
            await self._post_stop(run.base_url, run.run_id)
            log.info("Hermes run %s stopped: the turn ended early", run.run_id)
        self._untrack(run.run_id)
        if run.task is not None:
            run.task.cancel()
        # A turn still reading this run (stopped from another turn) ends now.
        run.queue.put_nowait(_END)

    async def _finish_late(self, run: _OpenRun) -> None:
        """Read a handed-off run to its end and speak its answer (``finish_late``)."""
        said = ""
        failed = False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + LATE_ANSWER_MAX_S
        try:
            while True:
                left = deadline - loop.time()
                if left <= 0:
                    log.info("Hermes run %s gave no answer in time; stopping it", run.run_id)
                    break
                try:
                    item = await asyncio.wait_for(run.queue.get(), left)
                except TimeoutError:
                    continue
                if item is _END or isinstance(item, BaseException):
                    failed = not run.finished
                    break
                name = str(item.get("event") or "") if isinstance(item, dict) else ""
                if name == "approval.request":
                    # Asked aloud like any approval; the user's next words answer it.
                    language = phrase_language()
                    self.pending_approval = _PendingApproval(
                        run=run, request_id=str(item.get("request_id") or ""), language=language
                    )
                    run.late = False
                    await self._announce(approval_question(item, language))
                    return
                if name in _FINISHED:
                    run.finished = True
                    failed = name != "run.completed" or bool(
                        item.get("completed") is False
                        or item.get("partial")
                        or item.get("failed")
                        or item.get("error")
                    )
                    output = item.get("output")
                    if not said and not failed and isinstance(output, str):
                        said = output
                for delta in self._deltas_for(name, item, run):
                    said += delta.content or ""
                if run.finished:
                    break
        finally:
            run.late = False
            if self.pending_approval is None or self.pending_approval.run is not run:
                await self._close(run)
        if run.cancelled:
            return
        if run.finished and not failed:
            if said.strip():
                await self._announce(said.strip())
            else:
                log.info("Hermes run %s finished late without words", run.run_id)
            return
        await self._announce(LATE_FAILED_MESSAGE)

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
        except Exception as exc:  # noqa: BLE001 — an unreachable Hermes counts as unresolved
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

    # --- the chat's approval card -----------------------------------------

    async def _ask_card(self, payload: dict[str, Any]) -> str:
        """Ask the typed chat's approval card about one Hermes approval:
        ``"once"`` on allow, ``"deny"`` on deny, timeout or no card."""
        from uuid import uuid4

        from jarvis.core.events import (
            ActionApprovalRequired,
            ActionApproved,
            ActionDenied,
            ActionProposed,
        )

        bus = _bus()
        if bus is None:
            return "deny"
        trace = uuid4()
        what = str(payload.get("description") or payload.get("command") or "").strip()
        tool = f"hermes:{payload.get('tool') or 'approval'}"
        answer: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        async def on_approved(event: ActionApproved) -> None:
            if event.trace_id == trace and not answer.done():
                answer.set_result("once")

        async def on_denied(event: ActionDenied) -> None:
            if event.trace_id == trace and not answer.done():
                answer.set_result("deny")

        bus.subscribe(ActionApproved, on_approved)
        bus.subscribe(ActionDenied, on_denied)
        publish_state("ACTING", detail="waiting for your approval", session_id=self.session_id)
        try:
            await bus.publish(
                ActionProposed(
                    trace_id=trace,
                    source_layer="brain.hermes",
                    tool_name=tool,
                    args={"command": str(payload.get("command") or ""), "description": what},
                    risk_tier="ask",
                )
            )
            await bus.publish(
                ActionApprovalRequired(
                    trace_id=trace,
                    source_layer="brain.hermes",
                    tool_name=tool,
                    args_preview=what[:200],
                    expires_at_ns=time.time_ns() + int(self._card_timeout_s * 1e9),
                    approval_ref=self._card_ref,
                )
            )
            return await asyncio.wait_for(answer, self._card_timeout_s)
        except TimeoutError:
            log.info("Hermes approval card timed out; denying")
            return "deny"
        finally:
            bus.unsubscribe(ActionApproved, on_approved)
            bus.unsubscribe(ActionDenied, on_denied)

    # --- "stop" ----------------------------------------------------------

    async def stop_everything(self) -> StopReport:
        """Stop what this session is doing: the live run, a parked approval,
        and delegated work still running in the background.

        A turn the user had just cut off (barge-in closes the stream before
        "stop" arrives as its own turn) counts as stopped too.
        """
        state = _session_state(self.session_id)
        report = StopReport()
        run = state.active
        if run is not None and not run.finished:
            run.cancelled = True
            await self._close(run)
            report.answer = True
        elif (
            state.checkpoint is not None
            and time.monotonic() - state.checkpoint.interrupted_at <= RECENT_INTERRUPT_S
        ):
            report.answer = True
        pending, state.pending_approval = state.pending_approval, None
        if pending is not None:
            await self._post_approval(pending.run, pending.request_id, "deny")
            await self._close(pending.run)
            report.approval = True
        if await self._stop_other_runs():
            report.answer = True
        watcher = state.watcher
        delegated = state.checkpoint is not None and state.checkpoint.delegated
        if watcher is not None and not watcher.done():
            watcher.cancel()
            state.watcher = None
            delegated = True
        if delegated:
            report.background = True
            _background(self._stop_delegations(), "hermes-stop-delegations")
        if report.count:
            publish_state("CANCELLED", session_id=self.session_id)
        log.info(
            "Stop: answer=%s approval=%s background=%s",
            report.answer, report.approval, report.background,
        )
        return report

    async def _stop_delegations(self) -> None:
        """Ask Hermes to stop its own subagents; only it can (``delegate_task`` stop)."""
        state = _session_state(self.session_id)
        kept = state.checkpoint
        req = BrainRequest(messages=(BrainMessage(role="user", content=STOP_DELEGATIONS_PROMPT),))
        try:
            async for _delta in self.complete(req):
                pass
        except Exception as exc:  # noqa: BLE001 — best effort; Hermes ends idle subagents on its own timeout
            log.warning("Hermes could not be asked to stop its subagents: %s", exc)
        finally:
            # The user's stopped task stays the one "continue" picks up.
            state.checkpoint = kept

    # --- background delegation results ---------------------------------

    def _watch_delivery(self) -> None:
        if self._watcher is not None and not self._watcher.done():
            return
        self._watcher = _background(self._deliver(), "hermes-delegation-result")

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
        except Exception as exc:  # noqa: BLE001 — the watcher tries again on its next tick
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

    def _deltas_for(self, name: str, item: dict[str, Any], run: _OpenRun) -> list[BrainDelta]:
        progress = run.progress
        if name == "message.delta":
            text = item.get("delta")
            if progress is not None and isinstance(text, str):
                progress.said += text
            return [BrainDelta(content=text if isinstance(text, str) and text else None)]
        if name == "tool.started":
            tool = tool_name_of(item)
            if tool == DELEGATE_TOOL:
                self._delegated = True
            if progress is not None:
                progress.tools.append(tool or "tool")
                progress.delegated = progress.delegated or tool == DELEGATE_TOOL
            publish_state("ACTING", detail=tool or "tool", session_id=self.session_id)
            self._publish_tool(item, tool or "tool", started=True)
            return [BrainDelta(agent_tools=(f"hermes:{tool or 'tool'}",))]
        if name == "tool.completed":
            self._publish_tool(item, tool_name_of(item) or "tool", started=False)
            return [BrainDelta()]
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

    def _publish_tool(self, item: dict[str, Any], tool: str, *, started: bool) -> None:
        """Hermes's tool calls on Jarvis's bus, so the run log and the
        activity view show what Hermes did (previews only, capped)."""
        from jarvis.core.events import ToolCallCompleted, ToolCallStarted

        if started:
            publish_event(
                ToolCallStarted(
                    source_layer="brain.hermes",
                    tool_name=f"hermes:{tool}",
                    args_preview=str(item.get("preview") or "")[:200],
                )
            )
            return
        try:
            duration_ms = float(item.get("duration") or 0.0) * 1000.0
        except (TypeError, ValueError):  # a malformed duration only loses the timing
            duration_ms = 0.0
        error = item.get("error")
        publish_event(
            ToolCallCompleted(
                source_layer="brain.hermes",
                success=not error,
                duration_ms=duration_ms,
                error=str(error)[:200] if isinstance(error, str) and error else None,
            )
        )

    def estimate_cost(self, req: BrainRequest) -> float:
        # Hermes bills (or not) on its own providers; Jarvis cannot see the
        # price of the model Hermes picks, so it books nothing here.
        del req
        return 0.0
