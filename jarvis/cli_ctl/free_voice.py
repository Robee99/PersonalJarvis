"""``jarvis system free-voice``: Hermes Agent as the brain of a full-time voice agent.

The flow it sets up::

    user -> Jarvis (voice, UI) -> Hermes Agent -> Hermes picks the model
         (local Qwen, local Gemma, a free cloud model) -> Hermes tools -> Jarvis

It configures the RUNNING app through its own API, so every write goes through
the same validated writers the settings UI uses (TOML, drift baseline and the
boot ENV layer stay in step). Nothing here edits ``jarvis.toml`` directly, and
nothing here configures Hermes: its models, local servers, MCP servers and
memory live in Hermes's own config. The one exception is the memory link
below, which adds a single MCP entry through Hermes's own ``config set``.

What it sets, and what it only reports:

* Hermes: its API server is probed with one real answer, and the provider and
  model Hermes resolved for it are reported. Each ``--check-model`` (a Hermes
  route alias or ``provider::model``, e.g. ``local-qwen::qwen``) is sent once
  the same way, so the report shows Hermes actually switching models at
  runtime, not just a config entry.
* Brain: the ``hermes`` provider is the main brain, with Hermes's reasoning
  pass switched off for speed (``thinking_budget = 0``).
* Route policy: Hermes answers every turn, Jarvis keeps no model routing of its
  own (no second tier, no Paperclip escalation), and paid providers stay
  deny-listed so a failure never becomes a paid fallback inside Jarvis.
* Missions: the sub-agent worker is Hermes too.
* Memory: Hermes gets Jarvis's wiki recall as an MCP server (``jarvis`` in
  Hermes's ``mcp_servers``, only the wiki tools), so notes imported into the
  memory orb are knowledge Hermes can look up. The Jarvis control key it needs
  is written to Hermes's own ``.env``, never to its ``config.yaml``.
* Voice: Pipeline mode (the brain answers; Realtime would hand every turn to the
  realtime provider instead). Speech-to-text and text-to-speech are switched to
  the first provider that works without paying, local ones first because they
  are the quickest.

Each step reports ``ok``, ``changed``, ``skipped`` or ``failed`` with a reason;
one failing step never stops the rest.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis.cli_ctl.client import ApiError
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

DEFAULT_HERMES = "http://127.0.0.1:8642"
HERMES_PROVIDER = "hermes"

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
#: Tried in order; the first one the app accepts wins. Local first: no network
#: round trip and no shared free-tier limit.
STT_CANDIDATES: tuple[str, ...] = ("nemotron-local", "faster-whisper")
TTS_CANDIDATES: tuple[str, ...] = ("piper-local",)

#: One Hermes answer may run a tool or load a local model; give it room.
HERMES_PROBE_TIMEOUT_S = 120.0


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


HttpPost = Callable[[str, dict[str, Any], dict[str, str], float], Any]


def _http_post(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
    import httpx

    resp = httpx.post(url, json=body, headers=headers, timeout=timeout)
    if resp.status_code == 401:
        raise RuntimeError("Hermes refused the API server key (HTTP 401)")
    resp.raise_for_status()
    return resp.json()


def _hermes_headers() -> dict[str, str]:
    from jarvis.plugins.brain.hermes import SESSION_KEY, read_api_server_key

    headers = {"X-Hermes-Session-Key": SESSION_KEY}
    key = read_api_server_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def probe_hermes(
    hermes: str,
    model: str = "",
    *,
    http_post: HttpPost = _http_post,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[bool, str]:
    """``(answered, detail)`` for one real Hermes answer, naming the model it used."""
    from jarvis.plugins.brain.hermes import model_fields

    body = {
        **model_fields(model),
        "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
        "stream": False,
        "model_options": {"reasoning": {"enabled": False}},
    }
    started = clock()
    try:
        reply = http_post(
            hermes.rstrip("/") + "/v1/chat/completions",
            body,
            _hermes_headers(),
            HERMES_PROBE_TIMEOUT_S,
        )
    except Exception as exc:  # the caller reports it as a failed step  # noqa: BLE001
        return False, f"{hermes} did not answer: {exc}"
    elapsed = clock() - started
    if not isinstance(reply, dict):
        return False, f"invalid Hermes response in {elapsed:.1f}s"
    choices = reply.get("choices") or [{}]
    text = str(((choices[0] or {}).get("message") or {}).get("content") or "").strip()
    runtime = reply.get("runtime") or {}
    used = (
        "/".join(str(runtime.get(k)) for k in ("provider", "model") if runtime.get(k))
        or "model not reported"
    )
    if not text:
        return False, f"answered with no text in {elapsed:.1f}s (ran on {used})"
    completion = reply.get("hermes") or {}
    if (
        completion.get("completed") is False
        or completion.get("failed")
        or completion.get("partial")
        or completion.get("error")
        or choices[0].get("finish_reason") in ("error", "length")
    ):
        return False, f"Hermes reported an incomplete or failed turn in {elapsed:.1f}s"
    if text.casefold().strip(" \t\r\n.!\"'`") != "ready":
        return False, f"Hermes did not return the expected readiness answer in {elapsed:.1f}s"
    return True, f"answered in {elapsed:.1f}s on {used}"


#: The Jarvis tools Hermes may call over MCP: wiki recall only. Everything else
#: (computer use, the browser, files) is Hermes's own.
HERMES_MEMORY_TOOLS: tuple[str, ...] = ("wiki-recall", "wiki-list")
#: The name of the ``.env`` variable that carries Jarvis's control key.
CONTROL_KEY_ENV = "JARVIS_CONTROL_KEY"

Runner = Callable[[list[str]], tuple[int, str]]


def _run(argv: list[str]) -> tuple[int, str]:
    done = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    return done.returncode, (done.stderr or done.stdout).strip()


def write_env_value(env_file: Any, name: str, value: str) -> None:
    """Set ``name=value`` in a dotenv file, replacing an earlier line."""
    from pathlib import Path

    path = Path(env_file)
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    kept = [line for line in lines if line.strip().partition("=")[0].strip() != name]
    kept.append(f"{name}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def hermes_memory_entry(jarvis_url: str) -> dict[str, Any]:
    """Hermes's ``mcp_servers.jarvis`` entry: Jarvis's wiki tools over MCP."""
    return {
        "url": jarvis_url.rstrip("/") + "/api/control/mcp/",
        "headers": {"Authorization": "Bearer ${" + CONTROL_KEY_ENV + "}"},
        "tools": {"include": list(HERMES_MEMORY_TOOLS), "resources": False, "prompts": False},
    }


def connect_hermes_memory(
    *,
    jarvis_url: str,
    control_key: str | None,
    env_file: Any,
    hermes_argv: list[str] | None,
    run: Runner = _run,
) -> tuple[str, str]:
    """Mount Jarvis's wiki recall in Hermes. Returns ``(status, detail)``."""
    if not control_key:
        return "failed", "Jarvis has no control key yet; start Jarvis once and run this again"
    if not hermes_argv:
        return "failed", "the hermes command is not on PATH"
    write_env_value(env_file, CONTROL_KEY_ENV, control_key)
    entry = json.dumps(hermes_memory_entry(jarvis_url))
    code, output = run([*hermes_argv, "config", "set", "mcp_servers.jarvis", entry])
    if code != 0:
        return "failed", f"hermes config set failed: {output[:300]}"
    return "changed", (
        "Hermes can search Jarvis's memory (wiki-recall, wiki-list); "
        "restart the Hermes gateway to load it"
    )


def keep_hermes_aux_free(*, hermes_argv: list[str] | None, run: Runner = _run) -> tuple[str, str]:
    """Keep Hermes's side models (vision, compression, titles) free.

    Hermes's auxiliary auto-chain can fall back to a paid OpenRouter model when
    an OpenRouter key is present; ``auxiliary.free_only`` skips that step
    unless the model is a ``:free`` one. Returns ``(status, detail)``.
    """
    if not hermes_argv:
        return "failed", "the hermes command is not on PATH"
    code, output = run([*hermes_argv, "config", "set", "auxiliary.free_only", "true"])
    if code != 0:
        return "failed", f"hermes config set failed: {output[:300]}"
    return "changed", "Hermes's side models (vision, summaries) use free models only"


def _call(client: Any, method: str, path: str, body: Any = None) -> Any:
    return client.request(method, path, json=body)


def run_free_voice(
    client: Any,
    *,
    hermes: str = DEFAULT_HERMES,
    check_models: tuple[str, ...] = (),
    http_post: HttpPost = _http_post,
    clock: Callable[[], float] = time.monotonic,
    link_memory: Callable[[], tuple[str, str]] | None = None,
    free_aux: Callable[[], tuple[str, str]] | None = None,
) -> FreeVoiceReport:
    report = FreeVoiceReport()

    try:
        _call(client, "GET", "/api/settings/voice-mode")
    except ApiError as exc:
        report.add("jarvis", "failed", f"Jarvis is not reachable: {exc}. Start it first.")
        return report
    report.add("jarvis", "ok", "running")

    # --- Hermes answers, and switches models when asked ----------------------
    answered, detail = probe_hermes(hermes, http_post=http_post, clock=clock)
    report.add("hermes", "ok" if answered else "failed", detail)
    for model in check_models:
        ok, model_detail = probe_hermes(hermes, model, http_post=http_post, clock=clock)
        report.add(f"hermes-model:{model}", "ok" if ok else "failed", model_detail)

    # --- Brain: Hermes for every turn ---------------------------------------
    brain_ok = False
    try:
        _call(client, "PUT", f"/api/providers/{HERMES_PROVIDER}/base-url", {"base_url": hermes})
        _call(client, "PUT", f"/api/providers/{HERMES_PROVIDER}/model", {"model": ""})
        _call(
            client,
            "POST",
            "/api/brain/switch",
            {"provider": HERMES_PROVIDER, "persist": True},
        )
        brain_ok = True
        report.add("brain", "changed", f"Hermes Agent at {hermes}; Hermes picks the model")
    except ApiError as exc:
        report.add("brain", "failed", str(exc))
    # A reasoning pass costs seconds before the first spoken word.
    try:
        _call(client, "PUT", f"/api/providers/{HERMES_PROVIDER}/thinking-budget", {"budget": 0})
        report.add(
            "thinking",
            "changed",
            "requested reasoning off; the selected model may require reasoning",
        )
    except ApiError as exc:
        report.add("thinking", "failed", str(exc))

    # --- Route policy: no model routing of Jarvis's own ---------------------
    policy: dict[str, Any] = {
        "enabled": True,
        "deep": {"provider": ""},
        "deny_providers": list(PAID_BRAIN_PROVIDERS),
        "escalation": {"enabled": False},
    }
    if brain_ok:
        policy["fast"] = {"provider": HERMES_PROVIDER}
    try:
        _call(client, "PUT", "/api/brain/route-policy", policy)
        report.add(
            "route-policy",
            "changed",
            "Hermes answers every turn, no second brain in Jarvis, paid providers blocked",
        )
    except ApiError as exc:
        report.add("route-policy", "failed", str(exc))

    # --- Missions: Hermes is the worker too ----------------------------------
    try:
        _call(
            client,
            "POST",
            "/api/jarvis-agent/switch",
            {"provider": HERMES_PROVIDER, "persist": True},
        )
        report.add("missions", "changed", "missions run on Hermes Agent")
    except ApiError as exc:
        report.add("missions", "failed", str(exc))

    # --- Voice: Pipeline, free speech in and out ----------------------------
    try:
        _call(client, "PUT", "/api/settings/voice-mode", {"mode": "pipeline", "persist": True})
        report.add("voice-mode", "changed", "Pipeline: Hermes answers every spoken turn")
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

    # --- Memory: Hermes can recall what the wiki knows -----------------------
    if link_memory is not None:
        try:
            report.add("hermes-memory", *link_memory())
        except OSError as exc:
            report.add("hermes-memory", "failed", f"{type(exc).__name__}: {exc}")

    # --- No paid fallback for Hermes's side models ----------------------------
    if free_aux is not None:
        try:
            report.add("hermes-free-aux", *free_aux())
        except OSError as exc:
            report.add("hermes-free-aux", "failed", f"{type(exc).__name__}: {exc}")

    return report


def link_memory_for(client: Any) -> Callable[[], tuple[str, str]]:
    """The production memory link: this machine's Hermes, Jarvis and key."""

    def link() -> tuple[str, str]:
        from jarvis.agent_chat.runner_cli import CliUnavailable, hermes_argv_prefix
        from jarvis.core.control_key import get_control_key
        from jarvis.plugins.brain.hermes import _hermes_home_candidates

        try:
            argv: list[str] | None = hermes_argv_prefix()
        except CliUnavailable:  # no hermes on PATH: reported as the step failure
            argv = None
        homes = _hermes_home_candidates()
        home = next((h for h in homes if (h / ".env").exists()), homes[0])
        return connect_hermes_memory(
            jarvis_url=str(getattr(client, "base_url", "http://127.0.0.1:47821")),
            control_key=get_control_key(),
            env_file=home / ".env",
            hermes_argv=argv,
        )

    return link


def free_aux_for() -> Callable[[], tuple[str, str]]:
    """The production free-only switch for this machine's Hermes."""

    def keep_free() -> tuple[str, str]:
        from jarvis.agent_chat.runner_cli import CliUnavailable, hermes_argv_prefix

        try:
            argv: list[str] | None = hermes_argv_prefix()
        except CliUnavailable:  # no hermes on PATH: reported as the step failure
            argv = None
        return keep_hermes_aux_free(hermes_argv=argv)

    return keep_free


def render_report(report: FreeVoiceReport) -> str:
    marks = {"ok": "OK  ", "changed": "SET ", "skipped": "SKIP", "failed": "FAIL"}
    lines = [f"{marks.get(s.status, s.status)}  {s.name}: {s.detail}" for s in report.steps]
    lines.append(
        "All set."
        if not report.failed
        else f"{len(report.failed)} step(s) need attention (FAIL lines above)."
    )
    return "\n".join(lines)
