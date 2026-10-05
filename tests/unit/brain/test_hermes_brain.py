"""Hermes Agent as Jarvis's brain: Hermes orchestrates, Jarvis is the front-end.

Pinned here, with Hermes's API server played by ``tests/fakes/fake_hermes_api``:

* each turn is one Hermes run without Jarvis's tools, and Hermes's own pick of
  the model is used unless the card names a Hermes route or ``provider::model``
  (both local models, Qwen and Gemma, are reached that way);
* the provider and model Hermes actually ran on come back and are recorded;
* tools Hermes ran count as evidence, so a real action is spoken and an empty
  promise is still caught;
* with Hermes as the brain, Jarvis's own shortcuts (local actions, skills,
  Agentic-IDE, force-spawn) never take the turn;
* the key stays in Hermes's .env and a remote Hermes is refused.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from jarvis.brain.manager import SUBAGENT_ONLY_BRAIN_PROVIDERS, BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainRoutePolicyConfig, load_config
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest
from jarvis.plugins.brain import hermes
from jarvis.plugins.brain.hermes import HermesBrain, build_instructions, model_fields
from tests.fakes.fake_hermes_api import FakeHermesApi, say


def _brain(server: FakeHermesApi, model: str | None = None) -> HermesBrain:
    brain = HermesBrain(model=model)
    brain.transport = server.transport
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


def test_hermes_gets_the_front_end_contract_not_jarvis_tool_rules() -> None:
    req = BrainRequest(
        messages=(BrainMessage(role="user", content="hi"),),
        system="ROUTER RULES: call open_app.\n\nReply in English.",
    )
    instructions = build_instructions(req)
    assert "open_app" not in instructions
    assert instructions.startswith(hermes.FRONT_END_INSTRUCTIONS)


@pytest.mark.asyncio
async def test_a_turn_streams_hermes_text_tool_evidence_and_the_model_it_used() -> None:
    server = FakeHermesApi(
        say("Opened Notepad.", tools=("computer_use",),
            runtime={"provider": "local-qwen", "model": "qwen"})
    )
    brain = _brain(server)
    deltas = await _collect(brain, _req("open notepad"))

    assert "".join(d.content or "" for d in deltas) == "Opened Notepad."
    assert [d.agent_tools for d in deltas if d.agent_tools] == [("hermes:computer_use",)]
    assert {"input_tokens": 40, "output_tokens": 3} in [d.usage for d in deltas if d.usage]
    assert brain.last_runtime == {"provider": "local-qwen", "model": "qwen"}
    sent = server.runs[0].body
    assert sent["model"] == "hermes-agent" and "provider" not in sent
    assert sent["input"] == "open notepad"
    assert "tools" not in sent and "conversation_history" not in sent
    assert server.runs[0].headers["x-hermes-session-key"] == hermes.SESSION_KEY


@pytest.mark.asyncio
async def test_qwen_and_gemma_are_reached_as_models_under_hermes() -> None:
    for card, provider, model in (
        ("local-qwen::qwen", "local-qwen", "qwen"),
        ("local-gemma::gemma-4-12b-qat", "local-gemma", "gemma-4-12b-qat"),
    ):
        server = FakeHermesApi(say("ok", runtime={"provider": provider, "model": model}))
        brain = _brain(server, model=card)
        await _collect(brain, _req("hello"))
        assert (server.runs[0].body["provider"], server.runs[0].body["model"]) == (provider, model)
        assert brain.last_runtime == {"provider": provider, "model": model}


@pytest.mark.asyncio
async def test_a_fast_turn_asks_hermes_to_skip_its_reasoning_pass() -> None:
    server = FakeHermesApi(say("4"))
    await _collect(_brain(server), _req("two plus two", reasoning_effort="none"))
    assert server.runs[0].body["model_options"] == {"reasoning": {"enabled": False}}


@pytest.mark.asyncio
async def test_the_key_is_read_from_hermes_env_in_place(_isolated: Path) -> None:
    (_isolated / ".env").write_text("API_SERVER_ENABLED=true\nAPI_SERVER_KEY=local-secret\n")
    server = FakeHermesApi(say("hi"))
    await _collect(_brain(server), _req("hi"))
    assert server.runs[0].headers["authorization"] == "Bearer local-secret"


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


@pytest.mark.asyncio
async def test_a_failed_run_is_raised_for_the_fallback_chain() -> None:
    async def fails(_server: Any, _run: Any):
        yield {"event": "run.failed", "error": "provider 429"}

    with pytest.raises(RuntimeError, match="429"):
        await _collect(_brain(FakeHermesApi(fails)), _req("hi"))


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


def hermes_of(mgr: BrainManager) -> HermesBrain:
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
    server = FakeHermesApi(say("I'll open Notepad now.", tools=("computer_use",)))
    hermes_of(hermes_manager).transport = server.transport

    answer = await hermes_manager.generate("open notepad", use_history=False)

    # Hermes's own tool run is the evidence, so the honesty guard keeps it.
    assert answer == "I'll open Notepad now."
    assert len(server.runs) == 1 and "tools" not in server.runs[0].body


@pytest.mark.asyncio
async def test_a_promise_without_any_hermes_tool_run_is_still_caught(
    hermes_manager: BrainManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_shortcuts(hermes_manager, monkeypatch)
    server = FakeHermesApi(say("I'll open Notepad now."))
    hermes_of(hermes_manager).transport = server.transport

    answer = await hermes_manager.generate("open notepad", use_history=False)

    assert answer != "I'll open Notepad now."
    assert "did not start" in answer
