"""Hermes Agent as Jarvis's brain: every turn goes to Hermes, which orchestrates.

Hermes Agent (Nous Research, MIT) is an agent with its own model providers,
tools, MCP servers, memory, skills and subagents. Its gateway exposes an
OpenAI-compatible API server (``hermes gateway`` with ``API_SERVER_ENABLED``,
``http://127.0.0.1:8642`` by default). This brain hands each turn to it, so the
flow is::

    user -> Jarvis (voice, UI) -> Hermes -> Hermes picks the model
         (local Qwen, local Gemma, a free or paid cloud model) -> Hermes tools
         -> answer -> Jarvis -> user

Jarvis does not offer Hermes its own tools and does not pick the model. The
model is whatever Hermes resolves for the request: its configured default, its
fallback providers, or a ``model_routes`` alias named on this card (``qwen``,
``gemma``). A card value ``provider::model`` asks for one Hermes provider
directly (Hermes always honours an explicit provider). The model Hermes actually
used comes back as ``runtime`` and is logged and kept on ``last_runtime``.

Because Hermes runs the tools, the manager treats it as the owner of the turn
(``orchestrates_tools``) and its own shortcuts stand down. The tools Hermes
reports running (``hermes.tool.progress`` stream events) are passed on as
``BrainDelta.agent_tools`` so Jarvis's honesty guard can still tell a real
action from a promise.

Credentials: none are stored in Jarvis. The API server key is read from
Hermes's own ``.env`` (``API_SERVER_KEY``) when the server is on this machine;
a remote Hermes is refused rather than sent a key it never asked for.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import BrainDelta, BrainRequest

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

#: Turns of conversation sent along; Hermes layers its own context on top.
HISTORY_TURNS = 12

#: Spoken-front-end contract layered on top of Hermes's own system prompt.
FRONT_END_INSTRUCTIONS = (
    "You are answering through JARVIS, the user's voice and desktop front-end. "
    "Your reply is spoken aloud: lead with the answer, in one to three short "
    "sentences of plain text, no markdown, no lists, unless the user asks for "
    "detail. Use your own tools to act on the computer, the browser and files. "
    "Say you did something only after a tool actually did it."
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
    """The request's ``model``/``provider`` for one card value.

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


def build_messages(req: BrainRequest) -> list[dict[str, str]]:
    """Front-end instructions plus the recent spoken conversation.

    Jarvis's own router prompt (its tool rules and tool names) is not sent:
    Hermes has its own tools and would chase names it does not have. The
    user's standing preferences and the turn's reply language are kept.
    """
    system_parts = [FRONT_END_INSTRUCTIONS]
    prefs = render_cli_standing_instructions(req.system)
    if prefs:
        system_parts.append(prefs)
    lang = extract_reply_language_directive(req.system)
    if lang:
        system_parts.append(lang)
    convo = [
        {"role": m.role, "content": m.content}
        for m in req.messages
        if getattr(m, "role", None) in ("user", "assistant")
        and isinstance(getattr(m, "content", None), str)
        and m.content.strip()
    ][-HISTORY_TURNS:]
    return [{"role": "system", "content": "\n\n".join(system_parts)}, *convo]


def tool_name_of(payload: Any) -> str:
    """The tool name in a ``hermes.tool.progress`` event (several spellings)."""
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


class HermesBrain:
    """Brain that delegates the whole turn to Hermes Agent's API server."""

    name: str = "hermes"
    context_window: int = 128_000
    # Tool turns are served: Hermes calls its own tools. Jarvis's tool list is
    # never sent, so no Jarvis tool call can come back.
    supports_tools: bool = True
    # Hermes takes images, but whether its model can see is Hermes's config;
    # screenshots stay with Hermes's own tools instead of Jarvis attachments.
    supports_vision: bool = False
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

    def request_body(self, req: BrainRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            **model_fields(self._model),
            "messages": build_messages(req),
            "stream": True,
        }
        if getattr(req, "reasoning_effort", None) == "none" or thinking_off_by_config():
            body["model_options"] = {"reasoning": {"enabled": False}}
        return body

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        import httpx

        base_url = configured_base_url()
        headers = self._headers(base_url)
        body = self.request_body(req)
        self.last_runtime = None
        timeout = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
            async with client.stream(
                "POST", base_url + "/v1/chat/completions", json=body, headers=headers
            ) as resp:
                if resp.status_code != 200:
                    detail = (await resp.aread()).decode("utf-8", "replace")[:300]
                    if resp.status_code == 401:
                        raise RuntimeError(
                            "Hermes Agent refused the request (401): the API server "
                            "key in Hermes's .env does not match."
                        )
                    raise RuntimeError(
                        f"Hermes Agent answered HTTP {resp.status_code}: {detail}"
                    )
                async for delta in self._parse_stream(resp.aiter_lines()):
                    yield delta
        if self.last_runtime:
            log.info(
                "Hermes answered with %s/%s",
                self.last_runtime.get("provider"),
                self.last_runtime.get("model"),
            )

    async def _parse_stream(self, lines: AsyncIterator[str]) -> AsyncIterator[BrainDelta]:
        """Server-sent events to deltas: text, tool evidence, usage, keepalive ticks."""
        event = ""
        data: list[str] = []
        async for raw in lines:
            line = raw.rstrip("\r")
            if line.startswith(":"):
                # Keepalive while a long tool runs: an empty delta tells the
                # caller's no-progress watchdog that Hermes is still working.
                yield BrainDelta()
                continue
            if line.startswith("event:"):
                event = line[6:].strip()
                continue
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
                continue
            if line or not data:
                continue
            payload_text, data = "\n".join(data), []
            current, event = event, ""
            if payload_text == "[DONE]":
                return
            try:
                payload = json.loads(payload_text)
            except ValueError:
                log.debug("Hermes stream: skipped a non-JSON event %r", current)
                continue
            for delta in self._deltas_for(current, payload):
                yield delta

    def _deltas_for(self, event: str, payload: Any) -> list[BrainDelta]:
        if event == "hermes.tool.progress":
            name = tool_name_of(payload)
            return [BrainDelta(agent_tools=(f"hermes:{name or 'tool'}",))]
        if event or not isinstance(payload, dict):
            return [BrainDelta()]
        runtime = payload.get("runtime")
        if isinstance(runtime, dict):
            self.last_runtime = runtime
        out: list[BrainDelta] = []
        for choice in payload.get("choices") or []:
            delta = (choice or {}).get("delta") or {}
            text = delta.get("content")
            out.append(
                BrainDelta(
                    content=text if isinstance(text, str) and text else None,
                    finish_reason=(choice or {}).get("finish_reason") or None,
                )
            )
        usage = payload.get("usage")
        if isinstance(usage, dict):
            out.append(
                BrainDelta(
                    usage={
                        "input_tokens": int(usage.get("prompt_tokens") or 0),
                        "output_tokens": int(usage.get("completion_tokens") or 0),
                    }
                )
            )
        return out or [BrainDelta()]

    def estimate_cost(self, req: BrainRequest) -> float:
        # Hermes bills (or not) on its own providers; Jarvis cannot see the
        # price of the model Hermes picks, so it books nothing here.
        del req
        return 0.0
