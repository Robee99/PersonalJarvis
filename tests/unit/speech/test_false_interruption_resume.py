"""A false interruption resumes the cut sentence instead of ending in silence.

A barge-in stops the answer as soon as sustained speech is confirmed over it.
When what followed held no words (a cough, a click, speaker echo, nothing at
all), the user did not take the floor, and going silent mid-sentence is the
worst outcome. These tests pin the decision (``FalseInterruptionResume``) and
its three pipeline seams: the empty / echo follow-up in the turn handler, real
words clearing it, and the session loop noticing a silent follow-up.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.protocols import AudioChunk, Transcript
from jarvis.speech import pipeline as pipeline_mod
from jarvis.speech.barge_resume import FalseInterruptionResume
from jarvis.speech.pipeline import SpeechPipeline, TurnTakingState


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


# --------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------


def test_cut_sentence_is_resumed_once() -> None:
    resume = FalseInterruptionResume(clock=_Clock())
    resume.arm("The meeting moved to Thursday.", "en")

    assert resume.take() == ("The meeting moved to Thursday.", "en")
    assert resume.take() is None


def test_real_words_drop_the_cut_sentence() -> None:
    resume = FalseInterruptionResume(clock=_Clock())
    resume.arm("The meeting moved to Thursday.", "en")

    resume.clear()

    assert resume.pending is False
    assert resume.take() is None


def test_quiet_check_counts_down_the_quiet_window_then_expires() -> None:
    clock = _Clock()
    resume = FalseInterruptionResume(clock=clock)
    assert resume.seconds_until_quiet_check() is None

    resume.arm("Sentence.", None)
    assert resume.seconds_until_quiet_check() == pytest.approx(
        FalseInterruptionResume.QUIET_WINDOW_S
    )
    clock.now += FalseInterruptionResume.QUIET_WINDOW_S + 0.5
    assert resume.seconds_until_quiet_check() == 0.0

    clock.now += FalseInterruptionResume.VALID_S
    assert resume.seconds_until_quiet_check() is None
    assert resume.take() is None


def test_repeated_false_interruptions_resume_only_once_in_a_row() -> None:
    # An echo path that keeps confirming barge-ins must not replay forever.
    resume = FalseInterruptionResume(clock=_Clock())
    resume.arm("Sentence.", "en")
    assert resume.take() is not None
    resume.arm("Sentence.", "en")
    assert resume.take() is None
    # Real words reset the budget.
    resume.clear()
    resume.arm("Next sentence.", "en")
    assert resume.take() == ("Next sentence.", "en")


def test_blank_sentence_is_not_armed() -> None:
    resume = FalseInterruptionResume(clock=_Clock())
    resume.arm("   ", "en")
    assert resume.pending is False


# --------------------------------------------------------------------------
# The turn handler: what the follow-up transcript says decides
# --------------------------------------------------------------------------


class _STT:
    def __init__(self, text: str) -> None:
        self.text = text

    async def transcribe_pcm(self, _pcm: bytes) -> Transcript:
        return Transcript(text=self.text, language="en", confidence=0.9, is_partial=False)


def _turn_pipeline(text: str) -> tuple[SpeechPipeline, list[tuple[str, str | None]]]:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._utterance_stt = _STT(text)
    pipe._stt_final_timeout_s = 1.0
    pipe._config = SimpleNamespace(latency=SimpleNamespace(enabled=False))
    pipe._bus = EventBus()
    pipe._latency_first_audio_marked = False
    pipe._latency_tracker = None
    pipe._last_endpoint_reason = None
    pipe._carry_pcm = bytearray()
    pipe._carry_started_monotonic = None
    spoken: list[tuple[str, str | None]] = []

    async def _speak(text: str, language: str | None = None, *, kind: str = "reply") -> bool:
        spoken.append((text, language))
        return False

    async def _set_turn_state(_state: TurnTakingState) -> None:
        return None

    async def _publish_event(_event: object) -> None:
        return None

    pipe._speak = _speak  # type: ignore[method-assign]
    pipe._set_turn_state = _set_turn_state  # type: ignore[method-assign]
    pipe._publish_event = _publish_event  # type: ignore[method-assign]
    pipe._false_interruption().arm("The meeting moved to Thursday.", "en")
    return pipe, spoken


@pytest.mark.asyncio
async def test_empty_follow_up_resumes_the_cut_sentence() -> None:
    pipe, spoken = _turn_pipeline("")

    assert await pipe._handle_utterance(b"\x00\x00" * 1600) is True

    assert spoken == [("The meeting moved to Thursday.", "en")]


@pytest.mark.asyncio
async def test_echo_follow_up_resumes_the_cut_sentence() -> None:
    pipe, spoken = _turn_pipeline("the meeting moved to")
    pipe._register_assistant_speech("The meeting moved to Thursday.")

    assert await pipe._handle_utterance(b"\x00\x00" * 1600) is True

    assert spoken == [("The meeting moved to Thursday.", "en")]


@pytest.mark.asyncio
async def test_real_words_after_a_barge_in_are_a_genuine_interruption() -> None:
    pipe, spoken = _turn_pipeline("wait, make it Friday instead")

    class _Reached(Exception):
        pass

    def _stop_after_the_guards(*_args: object, **_kwargs: object) -> str:
        raise _Reached

    # Everything past the guards is the ordinary brain turn; stop there.
    pipe._output_language = _stop_after_the_guards  # type: ignore[method-assign]
    with pytest.raises(_Reached):
        await pipe._handle_utterance(b"\x00\x00" * 1600)

    assert spoken == []
    assert pipe._false_interruption().pending is False


# --------------------------------------------------------------------------
# The session loop: nothing heard at all after the barge-in
# --------------------------------------------------------------------------


class _FakeTTS:
    name = "fake-tts"
    supports_streaming = True

    async def synthesize(
        self, text: str, voice: str | None = None, language_code: str | None = None
    ) -> AsyncIterator[AudioChunk]:
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]


class _FakeMic:
    async def __aenter__(self) -> _FakeMic:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    def stream(self):
        async def gen():
            await asyncio.Event().wait()  # pragma: no cover
            yield b""

        return gen()


class _SilentVAD:
    def utterances(self, _stream):
        async def gen():
            await asyncio.Event().wait()  # pragma: no cover
            yield b""

        return gen()


def _session_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[SpeechPipeline, list[tuple[str, str | None]]]:
    pipe = SpeechPipeline(
        tts=_FakeTTS(), bus=EventBus(), enable_whisper_wake=False, idle_timeout_s=30.0
    )
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda device=None, **kwargs: _FakeMic())
    pipe._vad = _SilentVAD()
    spoken: list[tuple[str, str | None]] = []

    async def _speak(text: str, language: str | None = None, *, kind: str = "reply") -> bool:
        spoken.append((text, language))
        return False

    pipe._speak = _speak  # type: ignore[method-assign]
    resume = FalseInterruptionResume()
    resume.QUIET_WINDOW_S = 0.05  # fast test; the production window is 2 s
    resume.arm("The meeting moved to Thursday.", "en")
    pipe._false_interruption_resume = resume
    return pipe, spoken


@pytest.mark.asyncio
async def test_silent_follow_up_resumes_without_waiting_for_the_idle_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe, spoken = _session_pipeline(monkeypatch)

    session = asyncio.create_task(pipe._active_session())
    try:
        for _ in range(40):
            if spoken:
                break
            await asyncio.sleep(0.05)
        assert spoken == [("The meeting moved to Thursday.", "en")]
        # Waking for the check is not the idle window: the call stays open.
        assert not session.done()
    finally:
        pipe._hangup_event.set()
        await asyncio.wait_for(session, timeout=2.0)


@pytest.mark.asyncio
async def test_follow_up_still_being_spoken_is_not_talked_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe, spoken = _session_pipeline(monkeypatch)
    # The VAD reported speech that has not ended yet: its transcript decides.
    pipe._vad_speech_after_endpoint = True

    session = asyncio.create_task(pipe._active_session())
    try:
        await asyncio.sleep(0.6)
        assert spoken == []
        assert not session.done()
    finally:
        pipe._hangup_event.set()
        await asyncio.wait_for(session, timeout=2.0)
