"""``jarvis system free-voice``: configures through the app API, reports each step."""

from __future__ import annotations

import json
from typing import Any

from jarvis.cli_ctl.client import ApiError
from jarvis.cli_ctl.free_voice import (
    PAID_BRAIN_PROVIDERS,
    STEP_MODEL,
    pick_gemma,
    probe_gateway,
    render_report,
    run_free_voice,
)


class FakeClient:
    def __init__(self, *, fail: dict[str, str] | None = None, mcp_content: str = "") -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.fail = fail or {}
        self.mcp_content = mcp_content

    def request(self, method: str, path: str, *, json: Any = None, **_: Any) -> Any:
        self.calls.append((method, path, json))
        key = f"{method} {path}"
        if key in self.fail:
            raise ApiError(self.fail[key], 409)
        if key == "GET /api/mcps/config/info":
            return {"content": self.mcp_content}
        if path.endswith("/enable"):
            return {"ok": True}
        return {"ok": True}

    def body(self, method: str, path: str) -> Any:
        return next(b for m, p, b in self.calls if m == method and p == path)


def _tool_reply(*_: Any) -> dict[str, Any]:
    return {"choices": [{"message": {"tool_calls": [{"id": "1"}]}}]}


def _tags(*_: Any) -> dict[str, Any]:
    return {"models": [{"name": "llama3:8b"}, {"name": "gemma3:12b-it-qat"}]}


def _run(client: FakeClient, **kw: Any):
    return run_free_voice(
        client, http_get=_tags, http_post=_tool_reply, home="C:/Users/rober", **kw
    )


def test_sets_step_brain_pipeline_voice_gemma_and_blocks_paid_providers() -> None:
    client = FakeClient()
    report = _run(client)

    assert not report.failed, render_report(report)
    assert client.body("PUT", "/api/providers/nous/base-url") == {"base_url": "http://127.0.0.1:11436"}
    assert client.body("PUT", "/api/providers/nous/model") == {"model": STEP_MODEL}
    assert client.body("POST", "/api/brain/switch")["provider"] == "nous"
    assert client.body("PUT", "/api/providers/ollama/model") == {"model": "gemma3:12b-it-qat"}
    assert client.body("PUT", "/api/settings/voice-mode")["mode"] == "pipeline"
    policy = client.body("PUT", "/api/brain/route-policy")
    assert policy["fast"] == {"provider": "nous", "model": STEP_MODEL}
    assert policy["deep"] == {"provider": "ollama", "model": "gemma3:12b-it-qat", "local": True}
    assert policy["escalation"]["agent"] == "hermes"
    assert "claude-api" in policy["deny_providers"]
    assert "gemini" not in PAID_BRAIN_PROVIDERS


def test_adds_missing_mcp_servers_without_touching_the_users_own() -> None:
    mine = {"mcpServers": {"windows-mcp": {"command": "C:/tools/windows-mcp.exe", "enabled": True}}}
    client = FakeClient(mcp_content="\ufeff" + json.dumps(mine))
    _run(client)

    written = client.body("PUT", "/api/mcps/config/raw")["mcpServers"]
    assert written["windows-mcp"]["command"] == "C:/tools/windows-mcp.exe"
    assert {"desktop-commander", "filesystem", "playwright", "fetch"} <= set(written)
    assert written["filesystem"]["args"][-1] == "C:/Users/rober"
    enabled = {p for m, p, _ in client.calls if p.endswith("/enable")}
    assert "/api/mcps/desktop-commander/enable" in enabled


def test_a_failing_step_is_reported_and_the_rest_still_runs() -> None:
    client = FakeClient(fail={"POST /api/stt/switch": "HTTP 409: no key"})
    report = _run(client)

    stt = next(s for s in report.steps if s.name == "stt")
    assert stt.status == "failed"
    assert any(p == "/api/tts/switch" for _, p, _ in client.calls)
    assert "need attention" in render_report(report)


def test_stt_falls_through_to_the_next_free_provider() -> None:
    class SttClient(FakeClient):
        def request(self, method: str, path: str, *, json: Any = None, **kw: Any) -> Any:
            if path == "/api/stt/switch" and json["provider"] == "gemini-api":
                self.calls.append((method, path, json))
                raise ApiError("HTTP 409: no key", 409)
            return super().request(method, path, json=json, **kw)

    report = _run(SttClient())
    assert next(s for s in report.steps if s.name == "stt").detail == "groq-api"


def test_unreachable_jarvis_stops_early_with_a_clear_reason() -> None:
    client = FakeClient(fail={"GET /api/settings/voice-mode": "unreachable"})
    report = _run(client)
    assert [s.name for s in report.steps] == ["jarvis"]
    assert "Start it first" in report.steps[0].detail


def test_gateway_without_tool_calls_is_flagged() -> None:
    reachable, tools, _ = probe_gateway(
        "http://g", http_post=lambda *_: {"choices": [{"message": {"content": "ok"}}]}
    )
    assert reachable and not tools

    def boom(*_: Any) -> Any:
        raise RuntimeError("rate-limited (HTTP 429)")

    reachable, tools, detail = probe_gateway("http://g", http_post=boom)
    assert not reachable and "429" in detail


def test_pick_gemma_prefers_12b_qat() -> None:
    assert pick_gemma(["gemma3:4b", "gemma3:12b", "gemma3:12b-it-qat"]) == "gemma3:12b-it-qat"
    assert pick_gemma(["gemma3:4b", "gemma3:12b"]) == "gemma3:12b"
    assert pick_gemma(["llama3"]) is None


def test_local_server_puts_gemma_on_the_openai_compatible_slot() -> None:
    def get(url: str, _t: float) -> dict[str, Any]:
        assert url == "http://127.0.0.1:11438/v1/models"
        return {"data": [{"id": "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf"}]}

    client = FakeClient()
    report = run_free_voice(
        client, http_get=get, http_post=_tool_reply, home="h",
        local_server="http://127.0.0.1:11438",
    )

    assert not report.failed, render_report(report)
    assert client.body("PUT", "/api/providers/local-openai/base-url") == {
        "base_url": "http://127.0.0.1:11438"
    }
    model = "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf"
    assert client.body("PUT", "/api/providers/local-openai/model") == {"model": model}
    deep = client.body("PUT", "/api/brain/route-policy")["deep"]
    assert deep == {"provider": "local-openai", "model": model, "local": True}
