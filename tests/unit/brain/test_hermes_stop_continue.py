"""A spoken "stop" stops Hermes, and "continue" picks the task back up.

Hermes's API server is played by ``tests/fakes/fake_hermes_api``.

* a turn the user cuts off keeps what it had done (a checkpoint);
* "stop" stops the live run, a parked approval and delegated background work,
  and says what it stopped, instead of "nothing was running";
* "continue" starts a run that carries the stopped request, what was already
  said and the tools already run, and the checkpoint is used once;
* "stop the music" is an order for Hermes, not a cancel;
* the turn's state goes out on the bus: THINKING, ACTING, INTERRUPTED,
  CANCELLED, RESUMED, COMPLETED.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.plugins.brain import hermes
from jarvis.plugins.brain.hermes import HermesBrain
from jarvis.speech.interrupt_intent import is_resume_intent
from tests.fakes.fake_hermes_api import FakeHermesApi, FakeRun, asks_approval, say


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(hermes, "configured_base_url", lambda: "http://127.0.0.1:8642")
    monkeypatch.setattr(hermes, "thinking_off_by_config", lambda: False)
    monkeypatch.setattr(hermes, "phrase_language", lambda: "en")
    monkeypatch.setattr(hermes, "_SESSIONS", {})
    states: list[tuple[str, str]] = []

    def publish(state: str, detail: str = "", session_id: str = "") -> None:
        del session_id
        states.append((state, detail))

    monkeypatch.setattr(hermes, "publish_state", publish)
    return states


@pytest.fixture
def states(_isolated: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return _isolated


def _brain(server: FakeHermesApi) -> HermesBrain:
    brain = HermesBrain()
    brain.join_conversation()
    brain.transport = server.transport
    return brain


def _req(text: str) -> BrainRequest:
    return BrainRequest(messages=(BrainMessage(role="user", content=text),))


async def _say(brain: HermesBrain, text: str) -> str:
    return "".join([d.content or "" async for d in brain.complete(_req(text))])


async def _long_essay(_server: FakeHermesApi, _run: FakeRun):
    yield {"event": "tool.started", "tool": "web_search", "preview": ""}
    yield {"event": "message.delta", "delta": "Lighthouses were first "}
    await asyncio.sleep(3600)
    yield {"event": "message.delta", "delta": "never"}


async def _cut_off(brain: HermesBrain, text: str) -> None:
    """Read a turn until it has spoken, then close it as barge-in does."""
    stream = brain.complete(_req(text))
    async for delta in stream:
        if delta.content:
            break
    await stream.aclose()


@pytest.mark.asyncio
async def test_stop_after_a_cut_off_says_it_stopped_and_continue_picks_it_up(
    states: list[tuple[str, str]],
) -> None:
    server = FakeHermesApi(_long_essay, say("built to guide ships."))
    brain = _brain(server)

    await _cut_off(brain, "write me an essay about lighthouses")
    assert server.runs[0].stopped
    report = await brain.stop_everything()
    assert report.answer and report.count == 1

    answer = await _say(brain, "continue")

    assert answer == "built to guide ships."
    resumed = server.runs[1].body["input"]
    assert "write me an essay about lighthouses" in resumed
    assert "Lighthouses were first" in resumed
    assert "web_search" in resumed
    names = [s for s, _ in states]
    assert names.index("INTERRUPTED") < names.index("CANCELLED") < names.index("RESUMED")
    assert names[-1] == "COMPLETED"
    assert ("ACTING", "web_search") in states


@pytest.mark.asyncio
async def test_the_checkpoint_is_used_once() -> None:
    server = FakeHermesApi(_long_essay, say("Done."), say("Continue what?"))
    brain = _brain(server)
    await _cut_off(brain, "write me an essay about lighthouses")
    await _say(brain, "continue")
    await _say(brain, "continue")
    assert server.runs[2].body["input"] == "continue"


@pytest.mark.asyncio
async def test_stop_from_another_turn_stops_the_live_run() -> None:
    server = FakeHermesApi(_long_essay)
    brain = _brain(server)
    typed = HermesBrain()
    typed.join_conversation()
    typed.transport = server.transport

    async def read() -> str:
        return await _say(typed, "write me an essay about lighthouses")

    task = asyncio.create_task(read())
    for _ in range(200):
        if server.runs and hermes._session_state(brain.session_id).active is not None:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    report = await brain.stop_everything()

    assert report.answer
    assert server.runs[0].stopped
    text = await asyncio.wait_for(task, 2)
    assert text.startswith("Lighthouses were first")
    assert hermes.take_checkpoint(brain.session_id) is not None


@pytest.mark.asyncio
async def test_stop_denies_a_parked_approval() -> None:
    server = FakeHermesApi(asks_approval("Deleted.", "Left it."))
    brain = _brain(server)
    await _say(brain, "delete my notes file")
    report = await brain.stop_everything()
    assert report.approval
    assert server.approvals[0]["choice"] == "deny"
    assert brain.pending_approval is None


@pytest.mark.asyncio
async def test_stop_with_nothing_running_reports_nothing(states: list[tuple[str, str]]) -> None:
    brain = _brain(FakeHermesApi())
    report = await brain.stop_everything()
    assert report.count == 0
    assert "CANCELLED" not in [s for s, _ in states]


@pytest.mark.asyncio
async def test_stop_asks_hermes_to_stop_delegated_work_and_announces_nothing() -> None:
    server = FakeHermesApi(
        say("I've handed it to an agent.", tools=("delegate_task",)),
        say("Stopped the agent."),
    )
    spoken: list[str] = []

    async def announce(text: str, _language: str) -> None:
        spoken.append(text)

    brain = _brain(server)
    brain.announce = announce
    brain.delivery_poll_s = 0.01
    await _say(brain, "check my repo with an agent")
    assert brain._watcher is not None

    report = await brain.stop_everything()
    assert report.background
    for _ in range(100):
        if len(server.runs) == 2 and server.runs[1].body:
            break
        await asyncio.sleep(0.01)
    assert server.runs[1].body["input"] == hermes.STOP_DELEGATIONS_PROMPT
    server.session_rows.append({"id": "41", "display_kind": hermes.DELIVERY_KIND})
    await asyncio.sleep(0.1)
    assert spoken == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("continue", True),
        ("Jarvis, continue please", True),
        ("carry on where you left off", True),
        ("mach weiter", True),  # i18n-allow: spoken command under test
        ("continúa", True),  # i18n-allow: spoken command under test
        ("continue with the report on Rome", False),
        ("stop", False),
    ],
)
def test_continue_is_recognised_only_as_a_whole_utterance(text: str, expected: bool) -> None:
    assert is_resume_intent(text) is expected


def test_resume_input_keeps_the_request_and_the_progress() -> None:
    cp = hermes.Checkpoint(request="plan my trip", said="Day one: Rome.", tools=["web_search"])
    text = hermes.resume_input(cp)
    assert "plan my trip" in text and "Day one: Rome." in text and "web_search" in text


# --- through the manager: what the user hears --------------------------------


@pytest.fixture
def hermes_manager(monkeypatch: pytest.MonkeyPatch):
    from jarvis.brain.manager import BrainManager
    from jarvis.core.bus import EventBus
    from jarvis.core.config import load_config

    monkeypatch.setattr("jarvis.core.config.get_secret_any", lambda _candidates: "test-key")
    cfg = load_config()
    cfg.brain.primary = "hermes"
    cfg.brain.reply_language = "en"
    return BrainManager.from_tier_config("router", cfg, EventBus(), provider_override="hermes")


def _manager_brain(manager: object, server: FakeHermesApi) -> HermesBrain:
    chain = manager._build_fallback_chain("fast")  # type: ignore[attr-defined]
    brain = manager._get_brain(*chain[0])  # type: ignore[attr-defined]
    for built in manager._brain_cache.values():  # type: ignore[attr-defined]
        if isinstance(built, HermesBrain):
            built.transport = server.transport
    return brain


@pytest.mark.asyncio
async def test_a_spoken_stop_says_it_stopped_hermes_and_continue_finishes(
    hermes_manager: object,
) -> None:
    server = FakeHermesApi(_long_essay, say("built to guide ships."))
    brain = _manager_brain(hermes_manager, server)
    await _cut_off(brain, "write me an essay about lighthouses")

    reply = await hermes_manager.generate("stop", use_history=False)  # type: ignore[attr-defined]
    assert reply.startswith("Stopped.") and "continue" in reply

    answer = await hermes_manager.generate("continue", use_history=False)  # type: ignore[attr-defined]
    assert answer == "built to guide ships."
    resumed = server.runs[1].body["input"]
    assert "write me an essay about lighthouses" in resumed
    assert "Current date" not in resumed.split("My request was:")[1]


@pytest.mark.asyncio
async def test_stop_the_music_is_an_order_for_hermes(hermes_manager: object) -> None:
    server = FakeHermesApi(say("Paused Spotify."))
    _manager_brain(hermes_manager, server)
    reply = await hermes_manager.generate("stop the music", use_history=False)  # type: ignore[attr-defined]
    assert reply == "Paused Spotify."
    assert server.runs[0].body["input"].endswith("stop the music")
