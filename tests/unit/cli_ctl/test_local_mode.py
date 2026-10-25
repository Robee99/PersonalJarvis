"""``jarvis system local``: Jarvis's own brain on a local model, no Hermes.

Owner's request 2026-10-25: "remove hermes". Contract under test: the local
server is proven with one real answer, ``local-openai`` becomes the brain,
Hermes and every paid or cloud brain are deny-listed, speech stays local, and
a failing step is reported while the rest still runs.
"""

from __future__ import annotations

import itertools
from typing import Any

from jarvis.cli_ctl.client import ApiError
from jarvis.cli_ctl.free_voice import PAID_BRAIN_PROVIDERS, render_report
from jarvis.cli_ctl.local_mode import LOCAL_PROVIDER, probe_local, run_local_mode


class FakeClient:
    def __init__(self, *, fail: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.fail = fail or {}

    def request(self, method: str, path: str, *, json: Any = None, **_: Any) -> Any:
        self.calls.append((method, path, json))
        key = f"{method} {path}"
        if key in self.fail:
            raise ApiError(self.fail[key], 409)
        return {"ok": True}

    def body(self, method: str, path: str) -> Any:
        return next(b for m, p, b in self.calls if m == method and p == path)

    def paths(self) -> list[str]:
        return [p for _, p, _ in self.calls]


class FakeServer:
    def __init__(self, reply: Any = None, error: Exception | None = None) -> None:
        self.urls: list[str] = []
        self.reply = reply if reply is not None else {
            "choices": [{"message": {"content": "ready"}}]
        }
        self.error = error

    def __call__(self, url: str, body: dict[str, Any], timeout: float) -> Any:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return self.reply


def _ticks() -> Any:
    counter = itertools.count()
    return lambda: float(next(counter))


def _steps(report: Any) -> dict[str, str]:
    return {s.name: s.status for s in report.steps}


def test_the_local_brain_answers_and_hermes_and_paid_brains_are_blocked() -> None:
    client = FakeClient()
    server = FakeServer()

    report = run_local_mode(client, http_post=server, clock=_ticks())

    assert not report.failed, render_report(report)
    assert server.urls == ["http://127.0.0.1:11443/v1/chat/completions"]
    assert client.body("POST", "/api/brain/switch") == {"provider": LOCAL_PROVIDER, "persist": True}
    assert client.body("PUT", f"/api/providers/{LOCAL_PROVIDER}/model") == {"model": "gemma-4b-qat"}
    policy = client.body("PUT", "/api/brain/route-policy")
    assert policy["fast"] == {"provider": LOCAL_PROVIDER}
    assert "hermes" in policy["deny_providers"]
    assert set(PAID_BRAIN_PROVIDERS) <= set(policy["deny_providers"])
    assert client.body("POST", "/api/stt/switch")["provider"] == "nemotron-local"
    assert client.body("POST", "/api/tts/switch")["provider"] == "piper-local"
    assert not any("hermes" in path for path in client.paths())


def test_a_failed_brain_switch_never_puts_the_local_provider_on_the_fast_tier() -> None:
    client = FakeClient(fail={"POST /api/brain/switch": "no such provider"})

    report = run_local_mode(client, http_post=FakeServer(), clock=_ticks())

    assert _steps(report)["brain"] == "failed"
    assert "fast" not in client.body("PUT", "/api/brain/route-policy")
    assert _steps(report)["voice-mode"] == "changed"


def test_a_local_brain_the_app_refuses_for_missions_is_kept_and_said() -> None:
    client = FakeClient(fail={"POST /api/jarvis-agent/switch": "not a worker"})

    report = run_local_mode(client, http_post=FakeServer(), clock=_ticks())

    assert _steps(report)["missions"] == "skipped"
    assert not report.failed


def test_unreachable_jarvis_stops_early() -> None:
    client = FakeClient(fail={"GET /api/settings/voice-mode": "connection refused"})

    report = run_local_mode(client, http_post=FakeServer(), clock=_ticks())

    assert [s.name for s in report.steps] == ["jarvis"]
    assert report.failed


def test_a_server_that_is_down_or_silent_is_reported() -> None:
    down = probe_local("http://127.0.0.1:1", "m", http_post=FakeServer(error=OSError("refused")))
    silent = probe_local(
        "http://127.0.0.1:1",
        "m",
        http_post=FakeServer(reply={"choices": [{"message": {"content": ""}}]}),
    )
    garbled = probe_local("http://127.0.0.1:1", "m", http_post=FakeServer(reply={"error": "x"}))

    assert not down[0] and "refused" in down[1]
    assert not silent[0] and "empty" in silent[1]
    assert not garbled[0]


def test_missing_local_speech_never_enables_an_api_speech_provider() -> None:
    client = FakeClient(
        fail={"POST /api/stt/switch": "not installed", "POST /api/tts/switch": "not installed"}
    )

    report = run_local_mode(client, http_post=FakeServer(), clock=_ticks())

    speech = ("/api/stt/switch", "/api/tts/switch")
    tried = [b["provider"] for _, p, b in client.calls if p in speech]
    assert set(tried) <= {"nemotron-local", "faster-whisper", "piper-local"}
    assert _steps(report)["stt"] == "failed"
