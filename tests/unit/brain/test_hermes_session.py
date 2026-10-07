"""One Hermes conversation for Jarvis: session, approvals, cut-offs, background results.

Hermes's API server is played by ``tests/fakes/fake_hermes_api``.

* every turn is a run in the same Hermes session, and only the newest turn is
  sent, because Hermes keeps the history;
* an approval Hermes asks for is spoken as a yes/no question, and the answer
  resolves that same run (one run, one approval call);
* a turn the user cuts off stops the run;
* a delegated task's result is fetched in the same session and spoken;
* with Hermes as the brain, realtime voice and the subscription voice profile
  stand down, so voice cannot reach a second brain.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain.route_policy import hermes_is_main_brain
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest
from jarvis.plugins.brain import hermes
from jarvis.plugins.brain.hermes import SESSION_ID, HermesBrain
from tests.fakes.fake_hermes_api import FakeHermesApi, FakeRun, asks_approval, say


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(hermes, "configured_base_url", lambda: "http://127.0.0.1:8642")
    monkeypatch.setattr(hermes, "thinking_off_by_config", lambda: False)
    monkeypatch.setattr(hermes, "phrase_language", lambda: "en")


def _brain(server: FakeHermesApi) -> HermesBrain:
    brain = HermesBrain()
    brain.join_conversation()
    brain.transport = server.transport
    return brain


def _req(*turns: str) -> BrainRequest:
    return BrainRequest(
        messages=tuple(
            BrainMessage(role="user" if i % 2 == 0 else "assistant", content=t)
            for i, t in enumerate(turns)
        )
    )


async def _say(brain: HermesBrain, *turns: str) -> tuple[str, list[BrainDelta]]:
    deltas = [d async for d in brain.complete(_req(*turns))]
    return "".join(d.content or "" for d in deltas), deltas


@pytest.mark.asyncio
async def test_every_turn_is_a_run_in_one_hermes_session_with_only_the_newest_turn() -> None:
    server = FakeHermesApi(say("Hi."), say("You asked me to say hi."))
    brain = _brain(server)

    await _say(brain, "say hi")
    answer, _ = await _say(brain, "say hi", "Hi.", "what did I ask?")

    assert answer == "You asked me to say hi."
    assert [r.body["session_id"] for r in server.runs] == [SESSION_ID, SESSION_ID]
    assert server.runs[1].body["input"] == "what did I ask?"
    assert "conversation_history" not in server.runs[1].body


@pytest.mark.asyncio
async def test_yes_resolves_the_same_hermes_run_and_it_carries_on() -> None:
    server = FakeHermesApi(asks_approval("Deleted notes.txt.", "Left it."))
    brain = _brain(server)

    question, _ = await _say(brain, "delete my notes file")
    assert "delete a file" in question and "Say yes or no" in question
    assert brain.pending_approval is not None

    answer, deltas = await _say(brain, "yes")

    assert answer == "Deleted notes.txt."
    assert server.approvals == [{"choice": "once", "request_id": "req-7"}]
    assert len(server.runs) == 1  # the same run, no new one
    assert any(d.agent_tools == ("hermes:terminal",) for d in deltas)
    assert brain.pending_approval is None


@pytest.mark.asyncio
async def test_no_denies_it_and_hermes_says_what_happened() -> None:
    server = FakeHermesApi(asks_approval("Deleted notes.txt.", "Okay, I left it."))
    brain = _brain(server)
    await _say(brain, "delete my notes file")
    answer, _ = await _say(brain, "no")
    assert answer == "Okay, I left it."
    assert server.approvals[0]["choice"] == "deny"


@pytest.mark.asyncio
async def test_an_unclear_answer_is_asked_again_without_touching_hermes() -> None:
    server = FakeHermesApi(asks_approval("Deleted.", "Left it."))
    brain = _brain(server)
    await _say(brain, "delete my notes file")
    answer, _ = await _say(brain, "maybe")
    assert "yes or no" in answer
    assert server.approvals == [] and brain.pending_approval is not None
    answer, _ = await _say(brain, "yes")
    assert answer == "Deleted."


@pytest.mark.asyncio
async def test_moving_on_denies_the_parked_run_before_the_new_turn() -> None:
    server = FakeHermesApi(asks_approval("Deleted.", "Left it."), say("It is sunny."))
    brain = _brain(server)
    await _say(brain, "delete my notes file")
    answer, _ = await _say(brain, "what's the weather like")
    assert server.approvals[0]["choice"] == "deny"
    assert answer == "It is sunny."
    assert len(server.runs) == 2


@pytest.mark.asyncio
async def test_a_cut_off_turn_stops_the_hermes_run() -> None:
    async def endless(_server: FakeHermesApi, _run: FakeRun):
        yield {"event": "message.delta", "delta": "Counting "}
        await asyncio.sleep(3600)
        yield {"event": "message.delta", "delta": "never"}

    server = FakeHermesApi(endless)
    brain = _brain(server)
    stream = brain.complete(_req("count to a million"))
    first = await anext(stream)
    while not first.content:
        first = await anext(stream)
    assert first.content == "Counting "
    await stream.aclose()
    assert server.runs[0].stopped


@pytest.mark.asyncio
async def test_a_background_delegation_result_comes_back_and_is_spoken() -> None:
    server = FakeHermesApi(
        say("I've handed the repository check to an agent.", tools=("delegate_task",)),
        say("The agent found one failing check: lint."),
    )
    spoken: list[tuple[str, str]] = []

    async def announce(text: str, language: str) -> None:
        spoken.append((text, language))

    brain = _brain(server)
    brain.announce = announce
    brain.delivery_poll_s = 0.01

    answer, _ = await _say(brain, "check my repo with an agent")
    assert "handed" in answer
    await asyncio.sleep(0.05)
    assert spoken == []  # nothing landed yet
    server.session_rows.append({"id": "41", "display_kind": hermes.DELIVERY_KIND})
    await asyncio.wait_for(brain._watcher, 2)

    assert spoken == [("The agent found one failing check: lint.", "en")]
    # Asked in the same session, as the newest turn only.
    assert server.runs[1].body["session_id"] == SESSION_ID
    assert server.runs[1].body["input"] == hermes.DELIVERY_PROMPT


def _config(primary: str = "hermes", mode: str = "realtime") -> Any:
    return SimpleNamespace(
        brain=SimpleNamespace(primary=primary, route_policy=SimpleNamespace(enabled=False)),
        voice=SimpleNamespace(mode=mode, profile=""),
    )


def test_with_hermes_as_the_brain_realtime_voice_stands_down() -> None:
    from jarvis.realtime.factory import _realtime_is_the_configured_voice_mode

    assert hermes_is_main_brain(_config())
    assert _realtime_is_the_configured_voice_mode(_config()) is False
    assert _realtime_is_the_configured_voice_mode(_config(primary="gemini")) is True


def test_hermes_on_the_fast_route_counts_as_the_brain() -> None:
    cfg = _config(primary="gemini")
    cfg.brain.route_policy = SimpleNamespace(enabled=True, fast=SimpleNamespace(provider="hermes"))
    assert hermes_is_main_brain(cfg)


# --- through the manager: what the user actually hears ----------------------


@pytest.fixture
def hermes_manager(monkeypatch: pytest.MonkeyPatch):
    from jarvis.brain.manager import BrainManager
    from jarvis.core.bus import EventBus
    from jarvis.core.config import BrainRoutePolicyConfig, load_config

    monkeypatch.setattr("jarvis.core.config.get_secret_any", lambda _candidates: "test-key")
    cfg = load_config()
    cfg.brain.primary = "hermes"
    cfg.brain.route_policy = BrainRoutePolicyConfig.model_validate(
        {"enabled": True, "fast": {"provider": "hermes"}}
    )
    return BrainManager.from_tier_config("router", cfg, EventBus(), provider_override="hermes")


@pytest.mark.asyncio
async def test_the_spoken_approval_round_trip_through_jarvis(hermes_manager: Any) -> None:
    server = FakeHermesApi(asks_approval("Deleted notes.txt.", "Left it."))
    chain = hermes_manager._build_fallback_chain("fast")
    hermes_manager._get_brain(*chain[0]).transport = server.transport

    question = await hermes_manager.generate("delete my notes file", use_history=False)
    assert "Say yes or no" in question
    answer = await hermes_manager.generate("yes", use_history=False)

    assert answer == "Deleted notes.txt."
    assert server.approvals == [{"choice": "once", "request_id": "req-7"}]
    assert len(server.runs) == 1


# --- the typed chat on the front page: the same brain and session -----------


@pytest.mark.asyncio
async def test_a_typed_chat_turn_on_the_hermes_seat_reaches_the_same_hermes_session(
    hermes_manager: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.agent_chat import runner_brain
    from jarvis.agent_chat.service import AgentChatService, resolve_runner
    from jarvis.agent_chat.store import AgentChatStore

    server = FakeHermesApi(say("Notepad is open."), say("I opened Notepad."))
    original_init = HermesBrain.__init__

    def init(self: HermesBrain, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.transport = server.transport

    monkeypatch.setattr(HermesBrain, "__init__", init)
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: hermes_manager)
    monkeypatch.setattr(runner_brain, "_agent_secret", lambda _resolver, _provider: None)
    voice_brain = hermes_manager._get_brain(*hermes_manager._build_fallback_chain("fast")[0])
    voice_brain.transport = server.transport

    assert resolve_runner("hermes", surface="jarvis") == "brain"
    svc = AgentChatService(
        AgentChatStore(":memory:"), assistant_name=lambda: "Jarvis", bus=lambda: None
    )
    session = svc.create_session(
        provider="hermes", model="stepfun/step-3.7-flash:free", effort="",
        cwd=str(tmp_path), permission_mode="ask", surface="jarvis",
    )
    queue = svc.subscribe(session.session_id)
    await svc.send(session.session_id, "Open Notepad and type hello from JARVIS")
    events: list[dict[str, Any]] = []
    async with asyncio.timeout(10):
        while not events or events[-1]["kind"] != "turn_finished":
            events.append(await queue.get())
    spoken = await hermes_manager.generate("What did you just do?", use_history=False)

    started = next(e for e in events if e["kind"] == "turn_started")["payload"]
    assert started["runner"] == "brain"
    typed = next(e for e in events if e["kind"] == "assistant_text")["payload"]["text"]
    assert typed == "Notepad is open."
    assert spoken == "I opened Notepad."
    typed_run, spoken_run = server.runs
    assert typed_run.body["session_id"] == spoken_run.body["session_id"] == SESSION_ID
    assert typed_run.body["model"] == "stepfun/step-3.7-flash:free"
    assert typed_run.body["input"].endswith("Open Notepad and type hello from JARVIS")
    assert "Always reply in English" in typed_run.body["instructions"]


def test_the_chat_pick_uses_the_hermes_bridge_even_when_voice_has_another_brain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.agent_chat import runner_brain
    from jarvis.agent_chat.service import resolve_runner

    other = SimpleNamespace(_config=_config(primary="gemini"))
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: other)
    assert resolve_runner("hermes", surface="jarvis") == "brain"
    assert resolve_runner("hermes", surface="agent") == "hermes-cli"
