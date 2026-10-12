"""The post-answer deaf window shrinks while microphone echo cancellation runs.

After every answer the session input is suppressed for a moment so the
speaker tail is not heard as the user. That also clips a quick reply ("yes")
right after a question. With the assistant's voice removed from the microphone
signal (``jarvis.audio.echo_canceller``) only the room's reverberation is left,
so the window drops to a short floor; without it nothing changes.
"""

from __future__ import annotations

import time

import pytest

from jarvis.audio import echo_canceller
from jarvis.speech import pipeline as pipeline_mod
from jarvis.speech.pipeline import SpeechPipeline


def _pipe() -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._post_tts_listen_suppression_s = 0.8
    pipe._input_suppressed_until_ns = 0
    return pipe


def _armed_seconds(pipe: SpeechPipeline, before_ns: int) -> float:
    return (pipe._input_suppressed_until_ns - before_ns) / 1e9


def test_full_window_without_echo_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(echo_canceller, "running", lambda: False)
    pipe = _pipe()

    before = time.time_ns()
    pipe._suppress_session_input_after_tts("response")

    assert _armed_seconds(pipe, before) == pytest.approx(0.8, abs=0.05)


def test_short_window_while_echo_cancellation_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(echo_canceller, "running", lambda: True)
    pipe = _pipe()

    before = time.time_ns()
    pipe._suppress_session_input_after_tts("response")

    assert _armed_seconds(pipe, before) == pytest.approx(
        pipeline_mod._AEC_POST_TTS_SUPPRESSION_S, abs=0.05
    )


def test_a_disabled_window_stays_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(echo_canceller, "running", lambda: True)
    pipe = _pipe()
    pipe._post_tts_listen_suppression_s = 0.0

    pipe._suppress_session_input_after_tts("response")

    assert pipe._input_suppressed_until_ns == 0
