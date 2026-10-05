"""``jarvis system free-voice``: one command for a free, full-time voice agent.

It configures the RUNNING app through its own API, so every write goes through
the same validated writers the settings UI uses (TOML, drift baseline and the
boot ENV layer stay in step). Nothing here edits ``jarvis.toml`` directly.

What it sets, and what it only reports:

* Brain: the Nous Portal provider on a local free gateway (keyless loopback),
  model ``stepfun/step-3.7-flash:free``. The gateway is probed first, including
  one tool call, because a brain that cannot call tools cannot open or close
  anything.
* Local model: the newest Gemma 12B on Ollama (QAT preferred) as the deep tier.
* Route policy: fast = Nous, deep = local Gemma, "ask Hermes" escalates to the
  Paperclip agent ``hermes``, and every paid provider is deny-listed so a free
  model failing never becomes a paid fallback.
* Voice: Pipeline mode (the selected brain answers; Realtime would hand every
  turn to the realtime provider instead). Speech-to-text and text-to-speech
  are switched to the first provider that works without paying.
* MCP: Windows control, browser, fetch, Desktop Commander and filesystem
  servers are added when missing and switched on; each result is reported.

Each step reports ``ok``, ``changed``, ``skipped`` or ``failed`` with a reason;
one failing step never stops the rest.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.cli_ctl.client import ApiError

STEP_MODEL = "stepfun/step-3.7-flash:free"
DEFAULT_GATEWAY = "http://127.0.0.1:11436"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
HERMES_AGENT = "hermes"
HERMES_PHRASES = ("ask hermes", "hand it to hermes", "let hermes do it")

#: Brain providers that bill per use or need a paid plan. Kept off every chain.
PAID_BRAIN_PROVIDERS: tuple[str, ...] = (
    "claude-api",
    "claude-cli",
    "openai",
    "codex",
    "grok",
    "grok-build",
    "openrouter",
    "vertex",
    "antigravity",
    "nvidia",
)
#: Tried in order; the first one the app accepts wins. Free keys or local.
STT_CANDIDATES: tuple[str, ...] = ("gemini-api", "groq-api", "faster-whisper")
TTS_CANDIDATES: tuple[str, ...] = ("gemini-flash-tts", "piper-local")


def default_mcp_servers(home: str) -> dict[str, dict[str, Any]]:
    """The PC-control servers, cloned from their official packages."""
    return {
        "windows-mcp": {
            "command": "uvx",
            "args": ["windows-mcp"],
            "description": "Open, close and arrange apps and windows; type and click.",
            "enabled": True,
        },
        "desktop-commander": {
            "command": "npx",
            "args": ["-y", "@wonderwhy-er/desktop-commander"],
            "description": "Terminal commands, processes and file edits.",
            "enabled": True,
        },
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", home],
            "description": "Read and write files in your user folder.",
            "enabled": True,
        },
        "playwright": {
            "command": "npx",
            "args": ["-y", "@playwright/mcp@latest"],
            "description": "Drive a browser: open pages, click, fill forms.",
            "enabled": True,
        },
        "fetch": {
            "command": "uvx",
            "args": ["mcp-server-fetch"],
            "description": "Fetch web pages as text.",
            "enabled": True,
        },
    }


@dataclass
class StepResult:
    name: str
    status: str  # ok | changed | skipped | failed
    detail: str = ""


@dataclass
class FreeVoiceReport:
    steps: list[StepResult] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.steps.append(StepResult(name, status, detail))

    @property
    def failed(self) -> list[StepResult]:
        return [s for s in self.steps if s.status == "failed"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": [s.__dict__ for s in self.steps],
            "failed": len(self.failed),
        }


HttpGet = Callable[[str, float], Any]
HttpPost = Callable[[str, dict[str, Any], float], Any]


def _http_get(url: str, timeout: float) -> Any:
    import httpx

    resp = httpx.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _http_post(url: str, body: dict[str, Any], timeout: float) -> Any:
    import httpx

    resp = httpx.post(url, json=body, timeout=timeout)
    if resp.status_code == 429:
        raise RuntimeError("rate-limited (HTTP 429)")
    resp.raise_for_status()
    return resp.json()


def probe_gateway(
    gateway: str, *, http_post: HttpPost = _http_post, timeout: float = 30.0
) -> tuple[bool, bool, str]:
    """``(reachable, calls_tools, detail)`` for one tool-call round trip."""
    body = {
        "model": STEP_MODEL,
        "max_tokens": 200,
        "messages": [{"role": "user", "content": "Open Notepad."}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "open_app",
                    "description": "Open an application on this computer.",
                    "parameters": {
                        "type": "object",
                        "properties": {"app_name": {"type": "string"}},
                        "required": ["app_name"],
                    },
                },
            }
        ],
    }
    try:
        reply = http_post(gateway.rstrip("/") + "/v1/chat/completions", body, timeout)
    except Exception as exc:  # the caller reports it as a failed step  # noqa: BLE001
        return False, False, f"{gateway} did not answer: {exc}"
    choices = (reply or {}).get("choices") or [{}]
    message = (choices[0] or {}).get("message") or {}
    if message.get("tool_calls"):
        return True, True, "answered with a tool call"
    return True, False, "answered, but without a tool call"


def pick_gemma(tags: list[str]) -> str | None:
    """The best Gemma 12B tag: QAT first, then any 12B, then any Gemma."""
    gemma = [t for t in tags if "gemma" in t.lower()]
    for pattern in (r"12b.*qat|qat.*12b", r"12b"):
        for tag in gemma:
            if re.search(pattern, tag.lower()):
                return tag
    return gemma[0] if gemma else None


#: A first MCP start downloads its package (npx/uvx), which takes a while.
MCP_START_TIMEOUT_S = 240.0


def _call(
    client: Any, method: str, path: str, body: Any = None, *, timeout_s: float | None = None
) -> Any:
    if timeout_s is None:
        return client.request(method, path, json=body)
    return client.request(method, path, json=body, timeout_s=timeout_s)


def _merge_mcp(existing: dict[str, Any], wanted: dict[str, dict[str, Any]]) -> list[str]:
    """Add the wanted servers that are missing; never overwrite the user's own."""
    servers = existing.setdefault("mcpServers", {})
    added: list[str] = []
    for name, entry in wanted.items():
        if name not in servers:
            servers[name] = dict(entry)
            added.append(name)
        elif isinstance(servers[name], dict) and not servers[name].get("enabled", True):
            servers[name]["enabled"] = True
    return added


def run_free_voice(
    client: Any,
    *,
    gateway: str = DEFAULT_GATEWAY,
    ollama: str = DEFAULT_OLLAMA,
    local_model: str | None = None,
    home: str | None = None,
    http_get: HttpGet = _http_get,
    http_post: HttpPost = _http_post,
) -> FreeVoiceReport:
    report = FreeVoiceReport()

    try:
        _call(client, "GET", "/api/settings/voice-mode")
    except ApiError as exc:
        report.add("jarvis", "failed", f"Jarvis is not reachable: {exc}. Start it first.")
        return report
    report.add("jarvis", "ok", "running")

    # --- Brain: Step 3.7 Flash through the free gateway -------------------
    reachable, calls_tools, detail = probe_gateway(gateway, http_post=http_post)
    if not reachable:
        report.add("step-gateway", "failed", detail)
    elif not calls_tools:
        report.add(
            "step-gateway", "failed",
            f"{detail}. Voice can talk but cannot open or close apps through it.",
        )
    else:
        report.add("step-gateway", "ok", detail)
    brain_ok = False
    try:
        _call(client, "PUT", "/api/providers/nous/base-url", {"base_url": gateway})
        _call(client, "PUT", "/api/providers/nous/model", {"model": STEP_MODEL})
        _call(client, "POST", "/api/brain/switch", {"provider": "nous", "persist": True})
        brain_ok = True
        report.add("brain", "changed", f"Nous Portal, {STEP_MODEL}, via {gateway}")
    except ApiError as exc:
        report.add("brain", "failed", str(exc))

    # --- Local model: Gemma on Ollama ---------------------------------------
    gemma = local_model
    if gemma is None:
        try:
            listing = http_get(ollama + "/api/tags", 5.0) or {}
            tags = [m.get("name", "") for m in listing.get("models", [])]
            gemma = pick_gemma(tags)
        except Exception as exc:  # noqa: BLE001 - reported below
            report.add("local-model", "failed", f"Ollama at {ollama} did not answer: {exc}")
    if gemma:
        try:
            _call(client, "PUT", "/api/providers/ollama/model", {"model": gemma})
            report.add("local-model", "changed", f"Ollama {gemma}")
        except ApiError as exc:
            report.add("local-model", "failed", str(exc))
            gemma = None
    elif not any(s.name == "local-model" for s in report.steps):
        report.add(
            "local-model", "skipped", "no Gemma model in Ollama (ollama pull gemma3:12b-it-qat)"
        )

    # --- Route policy -------------------------------------------------------
    policy: dict[str, Any] = {
        "enabled": True,
        "deny_providers": list(PAID_BRAIN_PROVIDERS),
        "escalation": {
            "enabled": True,
            "via": "paperclip",
            "agent": HERMES_AGENT,
            "trigger_phrases": list(HERMES_PHRASES),
        },
    }
    if brain_ok:
        policy["fast"] = {"provider": "nous", "model": STEP_MODEL}
    if gemma:
        policy["deep"] = {"provider": "ollama", "model": gemma, "local": True}
    try:
        _call(client, "PUT", "/api/brain/route-policy", policy)
        report.add(
            "route-policy", "changed",
            "fast Step 3.7 Flash, deep local Gemma, 'ask Hermes' via Paperclip, "
            "paid providers blocked",
        )
    except ApiError as exc:
        report.add("route-policy", "failed", str(exc))

    # --- Voice: Pipeline, free speech in and out ----------------------------
    try:
        _call(client, "PUT", "/api/settings/voice-mode", {"mode": "pipeline", "persist": True})
        report.add("voice-mode", "changed", "Pipeline: your selected brain answers every turn")
    except ApiError as exc:
        report.add("voice-mode", "failed", str(exc))
    for tier, candidates in (("stt", STT_CANDIDATES), ("tts", TTS_CANDIDATES)):
        reasons: list[str] = []
        for provider in candidates:
            try:
                body = {"provider": provider, "persist": True}
                _call(client, "POST", f"/api/{tier}/switch", body)
                report.add(tier, "changed", provider)
                break
            except ApiError as exc:  # collected; reported if no candidate works
                reasons.append(f"{provider}: {exc}")
        else:
            report.add(tier, "failed", "; ".join(reasons))

    # --- MCP servers --------------------------------------------------------
    try:
        info = _call(client, "GET", "/api/mcps/config/info") or {}
        raw = (info.get("content") or "").lstrip("﻿").strip()
        current = json.loads(raw) if raw else {"mcpServers": {}}
        if not isinstance(current, dict):
            current = {"mcpServers": {}}
        added = _merge_mcp(current, default_mcp_servers(home or str(Path.home())))
        _call(client, "PUT", "/api/mcps/config/raw", current)
        report.add(
            "mcp-config", "changed" if added else "ok", ", ".join(added) or "nothing missing"
        )
        for name in sorted(current.get("mcpServers", {})):
            entry = current["mcpServers"][name]
            if not isinstance(entry, dict) or not entry.get("enabled", True):
                continue
            try:
                result = (
                    _call(
                        client, "POST", f"/api/mcps/{name}/enable",
                        timeout_s=MCP_START_TIMEOUT_S,
                    )
                    or {}
                )
            except ApiError as exc:
                report.add(f"mcp:{name}", "failed", str(exc))
                continue
            if result.get("ok"):
                report.add(f"mcp:{name}", "ok", "connected")
            else:
                report.add(f"mcp:{name}", "failed", str(result.get("error") or "did not start"))
    except (ApiError, ValueError) as exc:
        report.add("mcp-config", "failed", str(exc))

    return report


def render_report(report: FreeVoiceReport) -> str:
    marks = {"ok": "OK  ", "changed": "SET ", "skipped": "SKIP", "failed": "FAIL"}
    lines = [f"{marks.get(s.status, s.status)}  {s.name}: {s.detail}" for s in report.steps]
    lines.append(
        "All set." if not report.failed
        else f"{len(report.failed)} step(s) need attention (FAIL lines above)."
    )
    return "\n".join(lines)
