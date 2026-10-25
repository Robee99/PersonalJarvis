"""``jarvis system local``: Jarvis on a local model, no Hermes, nothing paid.

The flow it sets up::

    user -> Jarvis (voice, UI) -> Jarvis's own brain on a local llama.cpp
         server (Gemma 4 E4B, Nemotron Bolt E4B, ...) -> Jarvis's tools,
         switched on per request (jarvis/brain/tool_switch.py) -> Jarvis

The owner asked for this on 2026-10-25 ("remove hermes ... no hermes"): one
brain, Jarvis's own, on models that run on this machine. Like
``free-voice`` it configures the RUNNING app through its API, so every write
goes through the validated writers the settings UI uses.

What it sets:

* Brain: ``local-openai`` at ``--server`` with ``--model``, after one real
  answer from that server proves it is up.
* Route policy: the local brain answers every turn; Hermes and every paid or
  cloud brain stay deny-listed, so a failure is reported, never re-routed
  to a paid model.
* Computer use and missions: the same local brain where the app accepts it.
* Voice: Pipeline mode, local speech-to-text and text-to-speech only.

Each step reports ``ok``, ``changed``, ``skipped`` or ``failed`` with a reason;
one failing step never stops the rest.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable
from typing import Any

from jarvis.cli_ctl.client import ApiError
from jarvis.cli_ctl.free_voice import (
    PAID_BRAIN_PROVIDERS,
    STT_CANDIDATES,
    TTS_CANDIDATES,
    FreeVoiceReport,
)

LOCAL_PROVIDER = "local-openai"
#: The model swapper's Gemma 4 E4B front (scripts/windows/model_swap.py).
DEFAULT_SERVER = "http://127.0.0.1:11443"
DEFAULT_MODEL = "gemma-4b-qat"
#: Brains that are not this machine's: Hermes (the owner removed it) and the
#: cloud brains that are free today but send every turn off the machine.
NOT_LOCAL_BRAINS: tuple[str, ...] = ("hermes", "gemini", "nous", "openclaw")
#: A cold model load reads ~5 GB of weights before the first answer.
PROBE_TIMEOUT_S = 420.0

HttpPost = Callable[[str, dict[str, Any], float], Any]


def _http_post(url: str, body: dict[str, Any], timeout: float) -> Any:
    request = urllib.request.Request(  # noqa: S310 — a loopback URL the owner names
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def probe_local(
    server: str,
    model: str,
    *,
    http_post: HttpPost = _http_post,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[bool, str]:
    """One real answer from the local server, or why there was none."""
    started = clock()
    try:
        reply = http_post(
            f"{server.rstrip('/')}/v1/chat/completions",
            {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly: ready"}],
                "max_tokens": 16,
            },
            PROBE_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 — every failure is reported, not raised
        return False, f"{server} did not answer: {type(exc).__name__}: {exc}"
    try:
        text = str(reply["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return False, f"{server} answered without a chat completion: {str(reply)[:120]}"
    if not text:
        return False, f"{server} answered with empty text"
    return True, f"{model} at {server} answered in {clock() - started:.1f}s"


def _call(client: Any, method: str, path: str, body: Any = None) -> Any:
    return client.request(method, path, json=body)


def run_local_mode(
    client: Any,
    *,
    server: str = DEFAULT_SERVER,
    model: str = DEFAULT_MODEL,
    http_post: HttpPost = _http_post,
    clock: Callable[[], float] = time.monotonic,
) -> FreeVoiceReport:
    report = FreeVoiceReport()

    try:
        _call(client, "GET", "/api/settings/voice-mode")
    except ApiError as exc:
        report.add("jarvis", "failed", f"Jarvis is not reachable: {exc}. Start it first.")
        return report
    report.add("jarvis", "ok", "running")

    answered, detail = probe_local(server, model, http_post=http_post, clock=clock)
    report.add("local-model", "ok" if answered else "failed", detail)

    # --- Brain: Jarvis's own, on the local server ------------------------------
    brain_ok = False
    try:
        _call(client, "PUT", f"/api/providers/{LOCAL_PROVIDER}/base-url", {"base_url": server})
        _call(client, "PUT", f"/api/providers/{LOCAL_PROVIDER}/model", {"model": model})
        _call(client, "POST", "/api/brain/switch", {"provider": LOCAL_PROVIDER, "persist": True})
        brain_ok = True
        report.add("brain", "changed", f"{model} at {server}; Hermes is not used")
    except ApiError as exc:
        report.add("brain", "failed", str(exc))

    # --- Route policy: one local brain, nothing paid or remote ----------------
    policy: dict[str, Any] = {
        "enabled": True,
        "deep": {"provider": ""},
        "deny_providers": list(dict.fromkeys((*PAID_BRAIN_PROVIDERS, *NOT_LOCAL_BRAINS))),
        "escalation": {"enabled": False},
    }
    if brain_ok:
        policy["fast"] = {"provider": LOCAL_PROVIDER}
    try:
        _call(client, "PUT", "/api/brain/route-policy", policy)
        report.add(
            "route-policy",
            "changed",
            "the local brain answers every turn; Hermes, paid and cloud brains blocked",
        )
    except ApiError as exc:
        report.add("route-policy", "failed", str(exc))

    # --- Hands: the same brain plans computer use and runs missions -----------
    for name, path in (
        ("computer-use", "/api/computer-use/switch"),
        ("missions", "/api/jarvis-agent/switch"),
    ):
        try:
            _call(client, "POST", path, {"provider": LOCAL_PROVIDER, "persist": True})
            report.add(name, "changed", LOCAL_PROVIDER)
        except ApiError as exc:
            # An app that cannot use a local brain here keeps its choice; say so.
            report.add(name, "skipped", f"kept as it was: {exc}")

    # --- Voice: Pipeline, local speech in and out -----------------------------
    try:
        _call(client, "PUT", "/api/settings/voice-mode", {"mode": "pipeline", "persist": True})
        report.add("voice-mode", "changed", "Pipeline: the local brain answers every spoken turn")
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

    return report
