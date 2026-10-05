"""Hermes Agent as Jarvis's brain: Hermes orchestrates, Jarvis is the front-end.

Pinned here, with Hermes's API server played by ``httpx.MockTransport``:

* the turn goes to Hermes without Jarvis's tools, and Hermes's own pick of the
  model is used unless the card names a Hermes route or ``provider::model``
  (both local models, Qwen and Gemma, are reached that way);
* the provider and model Hermes actually ran on come back and are recorded;
* tools Hermes ran count as evidence, so a real action is spoken and an empty
  promise is still caught;
* with Hermes as the brain, Jarvis's own shortcuts (local actions, skills,
  Agentic-IDE, force-spawn) never take the turn;
* the key stays in Hermes's .env and a remote Hermes is refused.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from jarvis.brain.manager import SUBAGENT_ONLY_BRAIN_PROVIDERS, BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainRoutePolicyConfig, load_config
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest
from jarvis.plugins.brain import hermes
from jarvis.plugins.brain.hermes import HermesBrain, build_messages, model_fields


def _sse(*events: tuple[str, dict[str, Any] | str]) -> bytes:
    out: list[str] = [": keepalive"]
    for name, payload in events:
        if name:
            out.append(f"event: {name}")
        out.append("data: " + (payload if isinstance(payload, str) else json.dumps(payload)))
        out.append("")
    return ("\n".join(out) + "\n").encode()


def _chunk(text: str = "", finish: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "choices": [{"delta": {"content": text} if text else {}, "finish_reason": finish}],
        **extra,
    }


class FakeHermesServer:
    """Answers like Hermes's /v1/chat/completions and remembers each request."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        return httpx.Response(200, content=self.body, headers={"content-type": "text/event-stream"})


def _brain(server: FakeHermesServer, model: str | None = None) -> HermesBrain:
    brain = HermesBrain(model=model)
    brain.transport = httpx.MockTransport(server)
    return brain


def _req(text: str, **kw: Any) -> BrainRequest:
    return BrainRequest(
        messages=(BrainMessage(role="user", content=text),),
        tools=({"type": "function", "function": {"name": "open_app"}},),
        system="ROUTER RULES: call open_app for apps.",
        **kw,
    )


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-home")
    monkeypatch.setattr(hermes, "configured_base_url", lambda: "http://127.0.0.1:8642")
    monkeypatch.setattr(hermes, "thinking_off_by_config", lambda: False)
    return home


async def _collect(brain: HermesBrain, req: BrainRequest) -> list[BrainDelta]:
    return [d async for d in brain.complete(req)]


@pytest.mark.parametrize(
    ("card", "expected"),
    [
        ("", {"model": "hermes-agent"}),
        ("hermes-agent", {"model": "hermes-agent"}),
        ("qwen", {"model": "qwen"}),
        ("local-qwen::qwen", {"provider": "local-qwen", "model": "qwen"}),
        (
            "local-gemma::gemma-4-12b-qat",
            {"provider": "local-gemma", "model": "gemma-4-12b-qat"},
        ),
        (
            "nous::stepfun/step-3.7-flash:free",
            {"provider": "nous", "model": "stepfun/step-3.7-flash:free"},
        ),
    ],
)
def test_the_card_value_becomes_hermes_model_selection(card: str, expected: dict) -> None:
    assert model_fields(card) == expected


def test_hermes_gets_the_conversation_not_jarvis_tool_rules() -> None:
    req = BrainRequest(
        messages=tuple(
            BrainMessage(role="user" if i % 2 == 0 else "assistant", content=f"turn {i}")
            for i in range(30)
        ),
        system="ROUTER RULES: call open_app.\n\nReply in English.",
    )
    messages = build_messages(req)
    assert messages[0]["role"] == "system"
    assert "open_app" not in messages[0]["content"]
    assert [m["content"] for m in messages[1:]] == [f"turn {i}" for i in range(18, 30)]


@pytest.mark.asyncio
async def test_a_turn_streams_hermes_text_tool_evidence_and_the_model_it_used() -> None:
    server = FakeHermesServer(
        _sse(
            ("hermes.tool.progress", {"tool": "open_app", "status": "started"}),
            ("", _chunk("Opened ")),
            ("", _chunk("Notepad.", "stop", runtime={"provider": "local-qwen", "model": "qwen"})),
            ("", {"choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 3}}),
            ("", "[DONE]"),
        )
    )
    brain = _brain(server)
    deltas = await _collect(brain, _req("open notepad"))

    assert "".join(d.content or "" for d in deltas) == "Opened Notepad."
    assert [d.agent_tools for d in deltas if d.agent_tools] == [("hermes:open_app",)]
    assert {"input_tokens": 40, "output_tokens": 3} in [d.usage for d in deltas if d.usage]
    assert brain.last_runtime == {"provider": "local-qwen", "model": "qwen"}
    sent = server.requests[0]
    assert sent["model"] == "hermes-agent" and "provider" not in sent
    assert "tools" not in sent
    assert sent["stream"] is True
    assert server.headers[0]["x-hermes-session-key"] == hermes.SESSION_KEY


@pytest.mark.asyncio
async def test_qwen_and_gemma_are_reached_as_models_under_hermes() -> None:
    for card, provider, model in (
        ("local-qwen::qwen", "local-qwen", "qwen"),
        ("local-gemma::gemma-4-12b-qat", "local-gemma", "gemma-4-12b-qat"),
    ):
        server = FakeHermesServer(
            _sse(("", _chunk("ok", "stop", runtime={"provider": provider, "model": model})))
        )
        brain = _brain(server, model=card)
        await _collect(brain, _req("hello"))
        assert (server.requests[0]["provider"], server.requests[0]["model"]) == (provider, model)
        assert brain.last_runtime == {"provider": provider, "model": model}


@pytest.mark.asyncio
async def test_every_free_cloud_picker_choice_explicitly_selects_nous() -> None:
    from jarvis.brain.model_catalog import ModelCatalog

    catalog = await ModelCatalog().list_models("hermes")
    cloud_choices = [m for m in catalog.models if m.id.startswith("nous::")]
    assert len(cloud_choices) == 7
    assert all(m.id.endswith(":free") for m in cloud_choices)
    assert catalog.models[0].id == "hermes-agent"
    for choice in cloud_choices:
        server = FakeHermesServer(_sse(("", _chunk("ready", "stop"))))
        await _collect(_brain(server, model=choice.id), _req("hello"))
        assert server.requests[0]["provider"] == "nous"
        assert server.requests[0]["model"] == choice.id.split("::", 1)[1]


def test_changing_hermes_picker_model_rebuilds_the_active_brain(
    hermes_manager: BrainManager,
) -> None:
    previous = _hermes_of(hermes_manager)
    for model in (
        "nous::poolside/laguna-xs-2.1:free",
        "nous::meituan/longcat-2.5-preview:free",
        "hermes-agent",
    ):
        assert hermes_manager.apply_provider_model("hermes", model)
        current = _hermes_of(hermes_manager)
        assert current is not previous
        assert model_fields(current._model) == model_fields(model)
        previous = current


def test_an_explicit_route_model_still_overrides_the_provider_card(
    hermes_manager: BrainManager,
) -> None:
    pinned = "nous::stepfun/step-3.7-flash:free"
    hermes_manager._config.brain.route_policy.fast.model = pinned
    hermes_manager.apply_provider_model("hermes", "nous::poolside/laguna-xs-2.1:free")
    assert _hermes_of(hermes_manager)._model == pinned


def test_a_selected_model_is_checked_against_route_denies(
    hermes_manager: BrainManager,
) -> None:
    hermes_manager._config.brain.route_policy.deny_model_prefixes = ["nous::blocked/"]
    hermes_manager.apply_provider_model("hermes", "nous::blocked/model")
    assert hermes_manager._build_fallback_chain("fast") == []


@pytest.mark.asyncio
async def test_a_fast_turn_asks_hermes_to_skip_its_reasoning_pass() -> None:
    server = FakeHermesServer(_sse(("", _chunk("4", "stop"))))
    await _collect(_brain(server), _req("two plus two", reasoning_effort="none"))
    assert server.requests[0]["model_options"] == {"reasoning": {"enabled": False}}


@pytest.mark.asyncio
async def test_the_key_is_read_from_hermes_env_in_place(_isolated: Path) -> None:
    (_isolated / ".env").write_text("API_SERVER_ENABLED=true\nAPI_SERVER_KEY=local-secret\n")
    server = FakeHermesServer(_sse(("", _chunk("hi", "stop"))))
    await _collect(_brain(server), _req("hi"))
    assert server.headers[0]["authorization"] == "Bearer local-secret"


@pytest.mark.asyncio
async def test_a_remote_hermes_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hermes, "configured_base_url", lambda: "http://10.0.0.5:8642")
    with pytest.raises(RuntimeError, match="not on this machine"):
        await _collect(HermesBrain(), _req("hi"))


@pytest.mark.asyncio
async def test_a_rejected_key_is_named() -> None:
    brain = HermesBrain()
    brain.transport = httpx.MockTransport(lambda _r: httpx.Response(401, content=b"no"))
    with pytest.raises(RuntimeError, match="401"):
        await _collect(brain, _req("hi"))


# --- the manager: Hermes owns the turn ------------------------------------


@pytest.fixture
def hermes_manager(monkeypatch: pytest.MonkeyPatch) -> BrainManager:
    monkeypatch.setattr("jarvis.core.config.get_secret_any", lambda _candidates: "test-key")
    cfg = load_config()
    cfg.brain.primary = "hermes"
    # What ``jarvis system free-voice`` sets: Hermes is the only chain entry,
    # so no test turn can reach a network provider.
    cfg.brain.route_policy = BrainRoutePolicyConfig.model_validate(
        {"enabled": True, "fast": {"provider": "hermes"}}
    )
    return BrainManager.from_tier_config("router", cfg, EventBus(), provider_override="hermes")


def test_hermes_is_a_main_brain_and_owns_tools(hermes_manager: BrainManager) -> None:
    assert "hermes" not in SUBAGENT_ONLY_BRAIN_PROVIDERS
    assert hermes_manager.active_provider == "hermes"
    assert hermes_manager._brain_orchestrates_tools() is True


def _hermes_of(mgr: BrainManager) -> HermesBrain:
    chain = mgr._build_fallback_chain("fast")
    assert [p for p, _ in chain] == ["hermes"]
    brain = mgr._get_brain(*chain[0])
    assert isinstance(brain, HermesBrain)
    return brain


class _ShortcutTaken(AssertionError):
    pass


def _forbid_shortcuts(mgr: BrainManager, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _taken(*_a: Any, **_k: Any) -> Any:
        raise _ShortcutTaken("a Jarvis shortcut took a turn Hermes owns")

    for name in (
        "_run_local_action_fast_path",
        "_run_wiki_ingest_fast_path",
        "_run_agentic_ide_fast_path",
        "_run_agentic_ide_close_fast_path",
        "_run_agentic_ide_spawn_fast_path",
        "_force_spawn_worker",
        "_maybe_dispatch_skill_mission",
        "_resolve_screen_context_turn",
    ):
        monkeypatch.setattr(mgr, name, _taken)
    monkeypatch.setattr(mgr, "_check_unsupported_intent", lambda *_a: "I can't do that.")


@pytest.mark.asyncio
async def test_open_an_app_goes_to_hermes_and_its_action_is_spoken(
    hermes_manager: BrainManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_shortcuts(hermes_manager, monkeypatch)
    server = FakeHermesServer(
        _sse(
            ("hermes.tool.progress", {"tool": "windows_mcp_launch"}),
            ("", _chunk("I'll open Notepad now.", "stop", runtime={"provider": "nous"})),
        )
    )
    _hermes_of(hermes_manager).transport = httpx.MockTransport(server)

    answer = await hermes_manager.generate("open notepad", use_history=False)

    # Hermes's own tool run is the evidence, so the honesty guard keeps it.
    assert answer == "I'll open Notepad now."
    assert len(server.requests) == 1 and "tools" not in server.requests[0]


@pytest.mark.asyncio
async def test_a_promise_without_any_hermes_tool_run_is_still_caught(
    hermes_manager: BrainManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_shortcuts(hermes_manager, monkeypatch)
    server = FakeHermesServer(_sse(("", _chunk("I'll open Notepad now.", "stop"))))
    _hermes_of(hermes_manager).transport = httpx.MockTransport(server)

    answer = await hermes_manager.generate("open notepad", use_history=False)

    assert answer != "I'll open Notepad now."
    assert "did not start" in answer
