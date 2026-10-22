"""A voice turn that gives up waiting lets its Hermes run finish and speaks the answer later.

Live 2026-10-12 02:51: "Hallo" reached Hermes when the session was due for
compaction. Hermes summarised the history for ~90 s before the model saw the
turn; Jarvis's 90 s voice ceiling stopped the run, the compaction was thrown
away, and every following turn started it again. The turn now ends without
stopping the run (``hermes.finish_late``), and the answer is announced when it
lands. Hermes's API server is played by ``tests/fakes/fake_hermes_api``.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.plugins.brain import hermes
from jarvis.plugins.brain.hermes import HermesBrain
from jarvis.speech.pipeline import _LATE_ANSWER_PHRASE, SpeechPipeline
from tests.fakes.fake_hermes_api import FakeHermesApi, FakeRun, say


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(hermes, "configured_base_url", lambda: "http://127.0.0.1:8642")
    monkeypatch.setattr(hermes, "thinking_off_by_config", lambda: False)
    monkeypatch.setattr(hermes, "phrase_language", lambda: "en")
    monkeypatch.setattr(hermes, "_SESSIONS", {})


class _Spoken(list[str]):
    """What Jarvis announced; ``heard`` is set by the first announcement."""

    def __init__(self) -> None:
        super().__init__()
        self.heard = asyncio.Event()

    async def wait(self) -> None:
        await asyncio.wait_for(self.heard.wait(), 2.0)


def _brain(server: FakeHermesApi) -> tuple[HermesBrain, _Spoken]:
    spoken = _Spoken()

    async def announce(text: str, _language: str) -> None:
        spoken.append(text)
        spoken.heard.set()

    brain = HermesBrain()
    brain.join_conversation()
    brain.transport = server.transport
    brain.announce = announce
    return brain, spoken


def _req(text: str) -> BrainRequest:
    return BrainRequest(messages=(BrainMessage(role="user", content=text),))


def _slow(go: asyncio.Event, text: str = "Hello. How can I help?") -> object:
    """A run that says nothing until ``go`` (a compaction, a cold prompt), then answers."""

    async def script(_server: FakeHermesApi, _run: FakeRun):
        await go.wait()
        yield {"event": "message.delta", "delta": text}
        yield {"event": "run.completed", "output": text}

    return script


async def _give_up(brain: HermesBrain, text: str) -> None:
    """Start a turn, let the run go silent, then end the turn the way the voice ceiling does."""
    stream = brain.complete(_req(text))
    await anext(stream)  # the first keepalive: the run is going
    assert hermes.finish_late() is True
    await stream.aclose()


@pytest.mark.asyncio
async def test_the_run_keeps_working_and_its_answer_is_spoken_when_it_lands() -> None:
    go = asyncio.Event()
    server = FakeHermesApi(_slow(go))
    brain, spoken = _brain(server)

    await _give_up(brain, "Hallo")
    await asyncio.sleep(0.05)
    assert not server.runs[0].stopped
    assert spoken == []

    go.set()
    await spoken.wait()
    assert spoken == ["Hello. How can I help?"]
    assert not server.runs[0].stopped
    assert hermes._session_state(hermes.SESSION_ID).active is None


@pytest.mark.asyncio
async def test_a_run_that_already_spoke_is_stopped_as_before() -> None:
    async def talks_then_hangs(_server: FakeHermesApi, _run: FakeRun):
        yield {"event": "message.delta", "delta": "Counting "}
        await asyncio.sleep(3600)
        yield {"event": "message.delta", "delta": "never"}

    server = FakeHermesApi(talks_then_hangs)
    brain, spoken = _brain(server)
    stream = brain.complete(_req("count to a million"))
    delta = await anext(stream)
    while not delta.content:
        delta = await anext(stream)

    assert hermes.finish_late() is False
    await stream.aclose()
    assert server.runs[0].stopped
    assert spoken == []


@pytest.mark.asyncio
async def test_a_new_turn_stops_the_late_run_and_nothing_old_is_spoken() -> None:
    go = asyncio.Event()
    server = FakeHermesApi(_slow(go, "Old answer."), say("New answer."))
    brain, spoken = _brain(server)

    await _give_up(brain, "Hallo")
    answer = "".join([d.content or "" async for d in brain.complete(_req("open notepad"))])

    assert answer == "New answer."
    assert server.runs[0].stopped
    go.set()
    await asyncio.sleep(0.05)
    assert spoken == []


@pytest.mark.asyncio
async def test_stop_stops_a_late_run() -> None:
    go = asyncio.Event()
    server = FakeHermesApi(_slow(go))
    brain, spoken = _brain(server)

    await _give_up(brain, "Hallo")
    report = await brain.stop_everything()

    assert report.answer
    assert server.runs[0].stopped
    go.set()
    await asyncio.sleep(0.05)
    assert spoken == []


@pytest.mark.asyncio
async def test_a_late_run_that_fails_says_so() -> None:
    go = asyncio.Event()

    async def fails(_server: FakeHermesApi, _run: FakeRun):
        await go.wait()
        yield {"event": "run.failed", "error": "model crashed"}

    server = FakeHermesApi(fails)
    brain, spoken = _brain(server)
    await _give_up(brain, "Hallo")
    go.set()
    await spoken.wait()
    assert spoken == [hermes.LATE_FAILED_MESSAGE]


def test_nothing_to_hand_off_without_a_hermes_run() -> None:
    assert hermes.finish_late() is False


@pytest.mark.asyncio
async def test_the_voice_ceiling_hands_the_run_off_and_says_to_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(hermes, "finish_late", lambda: calls.append("late") or True)
    p = SpeechPipeline.__new__(SpeechPipeline)
    p._brain_timeout_s = 5.0
    p._brain_hard_timeout_s = 0.2
    p._brain_stall_poll_s = 0.05
    p._brain_last_progress = time.monotonic()

    async def silent() -> tuple[str, bool]:
        while True:
            await asyncio.sleep(0.02)
            p._mark_brain_progress()  # keepalives

    with pytest.raises(TimeoutError):
        await p._run_brain_with_stall_guard(silent())
    assert calls == ["late"]
    assert p._brain_finishing_late is True

    phrases: list[str] = []

    async def speak(text: str, **_kw: object) -> None:
        phrases.append(text)

    async def set_state(_state: object) -> None:
        return None

    p._speak = speak
    p._set_turn_state = set_state
    p._turn_start_monotonic = 0.0
    await p._speak_brain_timeout("en", site="stream_stall")
    assert phrases == [_LATE_ANSWER_PHRASE["en"]]
