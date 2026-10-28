"""Quiet-tail guarantee for a finalized utterance (short commands came back empty).

A recognizer emits the last word of an utterance only once silence follows
it. A VAD endpoint already ends on its whole silence window, but a capture cut
by a key release ends on the last syllable. ``pad_trailing_silence`` appends
only the missing amount of digital silence and never touches the audio itself.
"""

from __future__ import annotations

import numpy as np

from jarvis.audio.vad import STT_TAIL_SILENCE_S, pad_trailing_silence

_RATE = 16_000


def _tone(seconds: float, amplitude: float = 0.3) -> bytes:
    t = np.arange(int(seconds * _RATE)) / _RATE
    wave = amplitude * np.sin(2 * np.pi * 220.0 * t)
    return (wave * 32767.0).astype(np.int16).tobytes()


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(seconds * _RATE)


def _seconds(pcm: bytes) -> float:
    return len(pcm) / 2 / _RATE


def test_speech_ending_on_the_last_word_gets_the_full_quiet_tail() -> None:
    speech = _tone(0.4)

    padded = pad_trailing_silence(speech)

    assert padded.startswith(speech)
    assert set(padded[len(speech) :]) == {0}
    assert abs(_seconds(padded) - (0.4 + STT_TAIL_SILENCE_S)) < 0.011


def test_only_the_missing_part_of_the_tail_is_added() -> None:
    speech = _tone(0.4) + _silence(0.2)

    padded = pad_trailing_silence(speech)

    assert padded.startswith(speech)
    assert abs(_seconds(padded) - (0.6 + STT_TAIL_SILENCE_S - 0.2)) < 0.011


def test_a_vad_endpoint_that_already_ends_on_silence_is_unchanged() -> None:
    utterance = _silence(0.48) + _tone(0.3) + _silence(1.5)

    assert pad_trailing_silence(utterance) is utterance


def test_room_tone_counts_as_silence() -> None:
    rng = np.random.default_rng(7)
    hiss = (rng.standard_normal(int(0.8 * _RATE)) * 0.001 * 32767.0).astype(np.int16)
    utterance = _tone(0.3) + hiss.tobytes()

    assert pad_trailing_silence(utterance) is utterance


def test_empty_audio_stays_empty() -> None:
    assert pad_trailing_silence(b"") == b""
