"""Empty Gemini Live turns after barge-ins get one re-ask, then an answer or an
honest line — never silence, never a loop.

Live 2026-10-04: after several barge-ins Gemini Live closed turns with nothing
in them (``audio=0.0s ... generation=none``) and one follow-up question went
unanswered. The empty turn itself was recovered through the surface TTS, but a
mute provider never sends the boundary that closes such a turn, so the user's
follow-up was appended to the ALREADY ANSWERED turn. Its own empty boundary then
skipped the empty-turn recovery (the turn belonged to a delivered delegate) and
closed in silence.

These tests run the real Gemini Live adapter over a fake SDK wire
(``tests/fakes/fake_gemini_live_sdk.py``) so the message shapes are the
server's own.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import jarvis.realtime.session as session_module
from jarvis.core.events import VoiceTurnCompleted
from jarvis.plugins.realtime.gemini_live import _GeminiLiveSession
from jarvis.voice.action_phrases import action_phrase
from tests.fakes.fake_gemini_live_sdk import (
    FakeGeminiLiveSdk,
    interrupted,
    model_spoke,
    turn_complete,
    user_said,
)
from tests.unit.realtime.test_session import FakeBrain, FakeBus, FakeProvider, _session

_REASK_MARKER = "without any spoken answer"
_TRUSTED_RESULT_MARKER = "<trusted_action_result>"


class _GeminiWireProvider(FakeProvider):
    """The real Gemini Live session adapter over the fake SDK wire."""

    def __init__(self, sdk: FakeGeminiLiveSdk) -> None:
        super().__init__([])
        self.sdk = sdk

    async def open_session(self, cfg):
        self.opened_with = cfg
        self.session = _GeminiLiveSession(
            session=self.sdk,
            connection_cm=SimpleNamespace(),
            client=SimpleNamespace(),
            session_id="gemini-wire",
        )
        return self.session


async def _until(predicate, limit_s: float = 3.0) -> None:
    async with asyncio.timeout(limit_s):
        while not predicate():  # noqa: ASYNC110 - bounded test poll
            await asyncio.sleep(0.01)


async def _settle(seconds: float = 0.3) -> None:
    await asyncio.sleep(seconds)


def _reasks(sdk: FakeGeminiLiveSdk) -> list[str]:
    return [text for text in sdk.text_inputs if _REASK_MARKER in text]


def _surface_lines(jsons: list[dict]) -> list[str]:
    return [str(m.get("text", "")) for m in jsons if m.get("type") == "error_spoken"]


@pytest.fixture(autouse=True)
def _short_readback_budget(monkeypatch):
    monkeypatch.setattr(session_module, "_DELEGATE_READBACK_WAIT_S", 0.2)


async def _open(brain, *, allow_classic_fallback: bool = True):
    sdk = FakeGeminiLiveSdk()
    provider = _GeminiWireProvider(sdk)
    jsons: list[dict] = []
    bus = FakeBus()
    sess = _session(provider, brain=brain, tool_mode="direct", jsons=jsons, bus=bus)
    sess.allow_classic_fallback = allow_classic_fallback
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await _until(lambda: getattr(provider, "session", None) is not None)
    return sess, sdk, jsons, bus


async def _barge_in_twice(sdk: FakeGeminiLiveSdk, last_words: str) -> None:
    """Two answers, each cut by the user well after any text Jarvis sent."""
    sdk.feed(user_said("Tell me a long story about penguins."), *model_spoke("Once"))
    await _settle(1.7)  # past the adapter's own-text attribution window
    sdk.feed(*model_spoke(" upon a time"))
    await _settle()
    # The wire order of a barge-in: the cut generation's edge and boundary,
    # then the user's words.
    sdk.feed(interrupted(), turn_complete(), user_said("No wait, tell me about cats."))
    await _settle()
    sdk.feed(*model_spoke("Cats are"))
    await _settle(1.7)
    sdk.feed(*model_spoke(" lovely"))
    await _settle()
    sdk.feed(interrupted(), turn_complete(), user_said(last_words))
    await _settle()
    # The live defect's shape: the answer generation never comes, the server
    # just closes the turn (``audio=0.0s ... generation=none``).
    sdk.feed(turn_complete())


@pytest.mark.asyncio
async def test_empty_turn_after_barge_in_is_reasked_exactly_once() -> None:
    brain = FakeBrain(replies=("Dogs are loyal companions.",))
    sess, sdk, jsons, _bus = await _open(brain)
    try:
        await _barge_in_twice(sdk, "Hmm, and dogs?")
        await _until(lambda: len(_reasks(sdk)) == 1)
        # Gemini closes the re-ask text with empty boundaries of its own; they
        # are not new misses and must not ask again.
        sdk.feed(turn_complete())
        await _settle()
        sdk.feed(turn_complete())
        await _until(lambda: "Dogs are loyal companions." in _surface_lines(jsons))
        await _settle()
        assert len(_reasks(sdk)) == 1
        assert "Hmm, and dogs?" in _reasks(sdk)[0]
        assert [call[0] for call in brain.calls] == ["Hmm, and dogs?"]
    finally:
        await sess.end(reason="test")


@pytest.mark.asyncio
async def test_follow_up_after_a_surface_answered_turn_is_answered() -> None:
    """The reproduced live defect: the follow-up must not join the old turn."""
    brain = FakeBrain(replies=("Dogs are loyal companions.", "Dogs eat meat and kibble."))
    sess, sdk, jsons, bus = await _open(brain)
    try:
        await _barge_in_twice(sdk, "Hmm, and dogs?")
        await _until(lambda: "Dogs are loyal companions." in _surface_lines(jsons))
        # The provider stays mute: no boundary closes the answered turn.
        await _settle()
        sdk.feed(user_said("What do dogs eat?"), turn_complete())
        await _until(lambda: "Dogs eat meat and kibble." in _surface_lines(jsons))
        assert [call[0] for call in brain.calls] == ["Hmm, and dogs?", "What do dogs eat?"]
        # One re-ask per user turn, not per boundary.
        assert len(_reasks(sdk)) == 2
        user_finals = [
            m["text"]
            for m in jsons
            if m.get("type") == "transcript" and m.get("role") == "user"
        ]
        assert "Hmm, and dogs? What do dogs eat?" not in user_finals
        completed = [e for e in bus.events if isinstance(e, VoiceTurnCompleted)]
        assert any(e.user_text == "Hmm, and dogs?" for e in completed)
    finally:
        await sess.end(reason="test")


@pytest.mark.asyncio
async def test_second_empty_turn_yields_the_honest_line_without_metered_fallback() -> None:
    """A transport that forbids usage-billed fallback says so instead of
    calling the Brain chain after its one re-ask stays mute."""
    brain = FakeBrain(replies=("must never be called",))
    sess, sdk, jsons, _bus = await _open(brain, allow_classic_fallback=False)
    try:
        await _barge_in_twice(sdk, "Hmm, and dogs?")
        await _until(lambda: len(_reasks(sdk)) == 1)
        sdk.feed(turn_complete())  # the re-ask comes back empty too
        honest = action_phrase("delegate_no_brain", "en")
        await _until(lambda: honest in _surface_lines(jsons))
        await _until(lambda: any(m.get("type") == "turn_complete" for m in jsons))
        await _settle()
        assert brain.calls == []
        assert len(_reasks(sdk)) == 1
        assert _surface_lines(jsons).count(honest) == 1
        assert not any(_TRUSTED_RESULT_MARKER in text for text in sdk.text_inputs)
    finally:
        await sess.end(reason="test")


@pytest.mark.asyncio
async def test_answered_turn_after_barge_in_is_untouched() -> None:
    brain = FakeBrain(replies=("must never be called",))
    sess, sdk, jsons, bus = await _open(brain)
    try:
        sdk.feed(user_said("Tell me a long story about penguins."), *model_spoke("Once"))
        await _settle(1.7)
        sdk.feed(interrupted(), turn_complete(), user_said("What do cats eat?"))
        await _settle()
        sdk.feed(*model_spoke("Cats eat meat."), turn_complete())
        await _until(
            lambda: any(
                isinstance(e, VoiceTurnCompleted) and e.user_text == "What do cats eat?"
                for e in bus.events
            )
        )
        await _settle()
        assert _reasks(sdk) == []
        assert brain.calls == []
        assert _surface_lines(jsons) == []
    finally:
        await sess.end(reason="test")


@pytest.mark.asyncio
async def test_a_burst_of_empty_boundaries_is_no_retry_storm() -> None:
    brain = FakeBrain(replies=("Dogs are loyal companions.",))
    sess, sdk, jsons, _bus = await _open(brain)
    try:
        await _barge_in_twice(sdk, "Hmm, and dogs?")
        await _until(lambda: len(_reasks(sdk)) == 1)
        for _ in range(8):
            sdk.feed(turn_complete())
            await asyncio.sleep(0.02)
        await _until(lambda: "Dogs are loyal companions." in _surface_lines(jsons))
        for _ in range(8):
            sdk.feed(turn_complete())
            await asyncio.sleep(0.02)
        await _settle(0.6)
        assert len(_reasks(sdk)) == 1
        assert len(brain.calls) == 1
        assert _surface_lines(jsons).count("Dogs are loyal companions.") == 1
    finally:
        await sess.end(reason="test")
