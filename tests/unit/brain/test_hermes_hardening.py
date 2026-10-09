"""Selectable models and truthful run outcomes on the shared Hermes bridge."""

import pytest

from jarvis.brain.manager import _TURN_OVERRIDE
from jarvis.brain.model_catalog import ModelCatalog
from jarvis.brain.turn_override import TurnOverride
from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.plugins.brain.hermes import HermesBrain, model_fields
from tests.fakes.fake_hermes_api import FakeHermesApi, say
from tests.unit.brain.test_hermes_brain import _isolated as _isolated
from tests.unit.brain.test_hermes_brain import hermes_manager as hermes_manager


def request() -> BrainRequest:
    return BrainRequest(messages=(BrainMessage(role="user", content="hello"),))


def agent(server: FakeHermesApi, model: str = "") -> HermesBrain:
    brain = HermesBrain(model=model)
    brain.transport = server.transport
    return brain


async def collect(brain):
    return [d async for d in brain.complete(request())]


@pytest.mark.asyncio
async def test_all_free_cloud_choices_explicitly_select_nous():
    catalog = await ModelCatalog().list_models("hermes")
    choices = [m for m in catalog.models if m.id.startswith("nous::")]
    # The live Nous free catalog GROWS over time (7 choices when this test was
    # written, 9 by 2026-10-09) — never pin an exact count, only a floor.
    assert len(choices) >= 7
    for choice in choices:
        server = FakeHermesApi(say("ready"))
        await collect(agent(server, choice.id))
        assert server.runs[0].body["provider"] == "nous"
        assert server.runs[0].body["model"] == choice.id.partition("::")[2]
        assert choice.id.endswith(":free")


def test_card_selection_rebuilds_the_hermes_brain(hermes_manager):
    previous = None
    for model in ("nous::poolside/laguna-xs-2.1:free", "hermes-agent"):
        assert hermes_manager.apply_provider_model("hermes", model)
        brain = hermes_manager._get_brain(*hermes_manager._build_fallback_chain("fast")[0])
        assert brain is not previous
        assert model_fields(brain._model) == model_fields(model)
        previous = brain


def test_tool_ownership_uses_the_chat_pick(hermes_manager):
    for provider, expected in (("nous", False), ("hermes", True)):
        token = _TURN_OVERRIDE.set(TurnOverride(provider=provider, model=""))
        try:
            assert hermes_manager._brain_orchestrates_tools() is expected
        finally:
            _TURN_OVERRIDE.reset(token)


@pytest.mark.asyncio
async def test_plan_never_dispatches_to_an_agent_with_unfiltered_tools(hermes_manager, monkeypatch):
    server = FakeHermesApi(say("changed a file"))
    brain = agent(server)
    monkeypatch.setattr(hermes_manager, "_get_brain", lambda *_a, **_k: brain)
    pick = TurnOverride(provider="hermes", model="", tool_context={"chat_read_only": True})
    reply = await hermes_manager.generate("open notepad", use_history=False, turn_override=pick)
    assert "did not start a new run" in reply
    assert pick.receipt.finish_reason == "policy_refusal"
    assert pick.receipt.policy_refusal == "read_only_unavailable"
    assert not server.runs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal",
    [
        None,
        {"event": "run.failed", "error": "private-error-body"},
        {"event": "run.cancelled"},
        {"event": "run.interrupted"},
        {"event": "run.completed", "completed": False},
        {"event": "run.completed", "partial": True},
    ],
)
async def test_failed_partial_or_disconnected_runs_are_never_success(terminal: dict | None):
    async def script(_server, _run):
        yield {"event": "message.delta", "delta": "partial reply"}
        if terminal is not None:
            yield terminal

    server = FakeHermesApi(script)
    with pytest.raises(RuntimeError, match="Hermes Agent") as caught:
        await collect(agent(server))
    assert "private-error-body" not in str(caught.value)
    if terminal is None:
        assert server.runs[0].stopped


@pytest.mark.asyncio
async def test_a_failed_run_sets_its_chat_receipt(hermes_manager, monkeypatch):
    async def script(_server, _run):
        yield {"event": "run.failed", "error": "private-error-body"}

    server = FakeHermesApi(script)
    brain = agent(server)
    monkeypatch.setattr(hermes_manager, "_get_brain", lambda *_a, **_k: brain)
    override = TurnOverride(provider="hermes", model="")
    await hermes_manager.generate("hello", use_history=False, turn_override=override)
    assert override.receipt.finish_reason == "error"
    assert len(server.runs) == 1


@pytest.mark.asyncio
async def test_an_empty_chain_records_failure(hermes_manager):
    hermes_manager._config.brain.route_policy.deny_providers = ["hermes"]
    override = TurnOverride(provider="hermes", model="")
    await hermes_manager.generate("hello", use_history=False, turn_override=override)
    assert override.receipt.finish_reason == "error"
