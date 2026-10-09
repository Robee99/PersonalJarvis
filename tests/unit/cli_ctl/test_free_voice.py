"""``jarvis system free-voice``: Hermes becomes the brain, through the app API only."""

from __future__ import annotations

import itertools
from typing import Any

from jarvis.cli_ctl.client import ApiError
from jarvis.cli_ctl.free_voice import (
    PAID_BRAIN_PROVIDERS,
    probe_hermes,
    render_report,
    run_free_voice,
)


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


class FakeHermes:
    """Hermes's API server: answers per requested provider/model, names the runtime."""

    def __init__(self, down: set[str] | None = None) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.down = down or set()

    def __call__(self, url: str, body: dict[str, Any], headers: dict[str, str], timeout: float):
        self.bodies.append(body)
        provider = body.get("provider") or "nous"
        model = body["model"] if body["model"] != "hermes-agent" else "step-3.7-flash:free"
        if provider in self.down:
            raise RuntimeError(f"{provider} server is not running")
        return {
            "choices": [{"message": {"content": "ready"}}],
            "runtime": {"provider": provider, "model": model},
        }


def _ticks() -> Any:
    counter = itertools.count()
    return lambda: float(next(counter))


def _run(client: FakeClient, hermes: FakeHermes | None = None, **kw: Any):
    return run_free_voice(client, http_post=hermes or FakeHermes(), clock=_ticks(), **kw)


def test_hermes_becomes_the_brain_for_everything_and_paid_providers_stay_blocked() -> None:
    client = FakeClient()
    report = _run(client)

    assert not report.failed, render_report(report)
    assert client.body("PUT", "/api/providers/hermes/base-url") == {
        "base_url": "http://127.0.0.1:8642"
    }
    # Setup reads the native preference; it must never reset a saved pin to Auto.
    assert client.body("GET", "/api/providers/hermes/models") is None
    assert not any(m == "PUT" and p == "/api/providers/hermes/model" for m, p, _ in client.calls)
    assert client.body("POST", "/api/brain/switch")["provider"] == "hermes"
    assert client.body("PUT", "/api/providers/hermes/thinking-budget") == {"budget": 0}
    policy = client.body("PUT", "/api/brain/route-policy")
    assert policy["fast"] == {"provider": "hermes"}
    # No second brain in Jarvis: no deep tier and no Paperclip side door.
    assert policy["deep"] == {"provider": ""}
    assert policy["escalation"] == {"enabled": False}
    assert "claude-api" in policy["deny_providers"]
    assert client.body("POST", "/api/jarvis-agent/switch")["provider"] == "hermes"
    assert client.body("PUT", "/api/settings/voice-mode")["mode"] == "pipeline"
    assert "gemini" not in PAID_BRAIN_PROVIDERS


def test_jarvis_does_not_start_its_own_mcp_servers_or_local_models() -> None:
    client = FakeClient()
    _run(client)
    assert not [p for p in client.paths() if "/mcps/" in p]
    assert not [p for p in client.paths() if "ollama" in p or "local-openai" in p]


def test_each_local_model_is_asked_through_hermes_and_reports_what_ran() -> None:
    hermes = FakeHermes()
    report = _run(
        FakeClient(),
        hermes,
        check_models=("local-qwen::qwen", "local-gemma::gemma-4-12b-qat"),
    )

    sent = [(b.get("provider"), b["model"]) for b in hermes.bodies]
    assert sent == [
        (None, "hermes-agent"),
        ("local-qwen", "qwen"),
        ("local-gemma", "gemma-4-12b-qat"),
    ]
    details = {s.name: s.detail for s in report.steps}
    assert "on local-qwen/qwen" in details["hermes-model:local-qwen::qwen"]
    assert "on local-gemma/gemma-4-12b-qat" in details["hermes-model:local-gemma::gemma-4-12b-qat"]


def test_a_local_model_that_is_not_running_is_reported_and_the_rest_still_runs() -> None:
    client = FakeClient()
    report = _run(
        client,
        FakeHermes(down={"local-gemma"}),
        check_models=("local-qwen::qwen", "local-gemma::gemma-4-12b-qat"),
    )
    assert [s.name for s in report.failed] == ["hermes-model:local-gemma::gemma-4-12b-qat"]
    assert client.body("POST", "/api/brain/switch")["provider"] == "hermes"


def test_an_older_app_without_the_thinking_setting_is_reported_not_fatal() -> None:
    client = FakeClient(fail={"PUT /api/providers/hermes/thinking-budget": "Not Found"})
    report = _run(client)
    assert [s.name for s in report.failed] == ["thinking"]
    assert client.body("PUT", "/api/settings/voice-mode")["mode"] == "pipeline"


def test_failed_steps_are_reported_and_the_rest_still_runs() -> None:
    client = FakeClient(fail={"POST /api/stt/switch": "no key"})
    report = _run(client)
    names = [s.name for s in report.failed]
    assert names == ["stt"]
    assert client.body("POST", "/api/tts/switch")["provider"] == "piper-local"
    assert "FAIL" in render_report(report)


def test_unreachable_jarvis_stops_early_with_a_clear_reason() -> None:
    client = FakeClient(fail={"GET /api/settings/voice-mode": "unreachable"})
    report = _run(client)
    assert [s.name for s in report.steps] == ["jarvis"]
    assert "Start it first" in report.steps[0].detail


def test_hermes_down_or_silent_is_flagged() -> None:
    def boom(*_: Any) -> Any:
        raise RuntimeError("connection refused")

    ok, detail = probe_hermes("http://127.0.0.1:8642", http_post=boom)
    assert not ok and "did not answer" in detail
    ok, detail = probe_hermes(
        "http://127.0.0.1:8642", http_post=lambda *_: {"choices": [{"message": {}}]}
    )
    assert not ok and "no text" in detail


def test_http_200_error_text_is_not_a_successful_local_model_probe() -> None:
    for reply in (
        {"choices": [{"message": {"content": "Could not connect to local Qwen"}}]},
        {
            "choices": [{"message": {"content": "ready"}, "finish_reason": "error"}],
            "hermes": {"completed": False, "failed": True},
        },
        {
            "choices": [{"message": {"content": "ready"}}],
            "hermes": {"partial": True},
        },
    ):
        ok, _ = probe_hermes(
            "http://127.0.0.1:8642",
            "local-qwen::qwen",
            http_post=lambda *_, result=reply: result,
        )
        assert not ok


def test_missing_runtime_metadata_is_reported_without_trusting_echoed_model() -> None:
    ok, detail = probe_hermes(
        "http://127.0.0.1:8642",
        http_post=lambda *_: {
            "model": "hermes-agent",
            "choices": [{"message": {"content": "Ready."}}],
        },
    )
    assert ok and "model not reported" in detail


def test_missing_local_speech_never_enables_an_api_speech_provider() -> None:
    client = FakeClient(
        fail={
            "POST /api/stt/switch": "not installed",
            "POST /api/tts/switch": "not installed",
        }
    )
    report = _run(client)
    assert {s.name for s in report.failed} == {"stt", "tts"}
    speech = [
        b["provider"]
        for _, path, b in client.calls
        if path in ("/api/stt/switch", "/api/tts/switch")
    ]
    assert speech == ["nemotron-local", "faster-whisper", "piper-local"]


def test_hermes_gets_connected_apps_with_native_consent_and_key_stays_in_its_env(tmp_path) -> None:
    import json

    from jarvis.cli_ctl.free_voice import CONTROL_KEY_ENV, connect_hermes_memory

    env_file = tmp_path / ".env"
    env_file.write_text("API_SERVER_KEY=abc\nJARVIS_CONTROL_KEY=old\n", encoding="utf-8")
    calls: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return 0, ""

    status, _detail = connect_hermes_memory(
        jarvis_url="http://127.0.0.1:47821/",
        control_key="ck-new",
        env_file=env_file,
        hermes_argv=["hermes"],
        run=run,
    )

    assert status == "changed"
    assert env_file.read_text(encoding="utf-8").splitlines() == [
        "API_SERVER_KEY=abc",
        f"{CONTROL_KEY_ENV}=ck-new",
    ]
    (argv,) = calls
    assert argv[:4] == ["hermes", "config", "set", "mcp_servers.jarvis"]
    entry = json.loads(argv[4])
    assert entry["url"] == "http://127.0.0.1:47821/api/control/mcp/hermes"
    assert entry["headers"] == {"Authorization": "Bearer ${JARVIS_CONTROL_KEY}"}
    assert entry["tools"] == {"resources": False, "prompts": False}
    assert entry["trust"] == "full"
    assert entry["sampling"] == {"enabled": False}
    assert entry["elicitation"] == {"enabled": True, "timeout": 300}
    assert entry["timeout"] > entry["elicitation"]["timeout"]
    assert "ck-new" not in argv[4], "the key never goes on a command line or into config.yaml"


def test_the_memory_link_reports_what_is_missing(tmp_path) -> None:
    from jarvis.cli_ctl.free_voice import connect_hermes_memory

    def never(_argv: list[str]) -> tuple[int, str]:
        raise AssertionError("hermes must not run")

    no_key = connect_hermes_memory(
        jarvis_url="http://x", control_key=None, env_file=tmp_path / ".env",
        hermes_argv=["hermes"], run=never,
    )
    no_hermes = connect_hermes_memory(
        jarvis_url="http://x", control_key="k", env_file=tmp_path / ".env",
        hermes_argv=None, run=never,
    )
    assert no_key[0] == no_hermes[0] == "failed"
    assert not (tmp_path / ".env").exists()


def test_hermes_side_models_are_kept_free() -> None:
    from jarvis.cli_ctl.free_voice import keep_hermes_aux_free

    calls: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return 0, ""

    assert keep_hermes_aux_free(hermes_argv=["hermes"], run=run)[0] == "changed"
    assert calls == [["hermes", "config", "set", "auxiliary.free_only", "true"]]
    assert keep_hermes_aux_free(hermes_argv=None, run=run)[0] == "failed"


def test_hermes_context_is_capped_for_fast_replies() -> None:
    from jarvis.cli_ctl.free_voice import LEAN_CONTEXT_TOKENS, keep_hermes_context_lean

    calls: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return 0, ""

    assert keep_hermes_context_lean(hermes_argv=["hermes"], run=run)[0] == "changed"
    assert calls == [
        ["hermes", "config", "set", "compression.threshold_tokens", str(LEAN_CONTEXT_TOKENS)]
    ]


def test_hermes_gets_its_computer_use_for_jarvis_runs() -> None:
    from jarvis.cli_ctl.free_voice import enable_hermes_computer_use

    calls: list[list[str]] = []
    installs: list[list[str]] = []
    statuses = iter(["cua-driver: not installed", "cua-driver 0.4.2 ready"])

    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return 0, next(statuses) if argv[-1] == "status" else "✓ Enabled: computer_use"

    def install(argv: list[str]) -> tuple[int, str]:
        installs.append(argv)
        return 0, "installed"

    status, _detail = enable_hermes_computer_use(hermes_argv=["hermes"], run=run, install=install)
    assert status == "changed"
    assert installs == [["hermes", "computer-use", "install"]]
    assert calls[-1] == ["hermes", "tools", "enable", "--platform", "api_server", "computer_use"]


def test_a_missing_computer_use_driver_is_reported_and_not_enabled() -> None:
    from jarvis.cli_ctl.free_voice import enable_hermes_computer_use

    calls: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return 0, "cua-driver: not installed"

    status, detail = enable_hermes_computer_use(
        hermes_argv=["hermes"], run=run, install=lambda _argv: (1, "download failed")
    )
    assert status == "failed" and "download failed" in detail
    assert not any("enable" in argv for argv in calls)
