"""A streamed answer whose every sentence fails to synthesise must not end mute.

``_synth_into`` logs a provider error and moves on, so a broken voice provider
(no key, depleted quota, wrong region) used to leave the user with an answer on
screen, no audio and no sign of why. The turn now publishes an
``ErrorOccurred`` on the ``speech.tts`` layer that the desktop shows as a toast.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import ErrorOccurred
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline


@dataclass
class _BrokenTTS:
    name: str = "broken-tts"
    supports_streaming: bool = True

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        raise RuntimeError("no API key for the voice provider")
        yield  # pragma: no cover - makes this an async generator


@dataclass
class _HealthyTTS:
    name: str = "healthy-tts"
    supports_streaming: bool = True

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        yield AudioChunk(pcm=b"\x00\x01", sample_rate=24_000, timestamp_ns=0, channels=1)


@dataclass
class _Player:
    played: int = 0

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        async for _chunk in chunks:
            self.played += 1

    def stop(self) -> None:
        pass


class _Brain:
    async def generate_stream(self, text: str) -> AsyncIterator[str]:
        yield "Es ist zwölf Uhr. "
        yield "Noch etwas?"


@dataclass
class _Recorder:
    errors: list[ErrorOccurred] = field(default_factory=list)

    async def __call__(self, event: ErrorOccurred) -> None:
        self.errors.append(event)


async def _run_turn(tts: object) -> tuple[_Recorder, _Player]:
    bus = EventBus()
    recorder = _Recorder()
    bus.subscribe(ErrorOccurred, recorder)
    pipeline = SpeechPipeline(tts=tts, bus=bus, enable_whisper_wake=False)
    player = _Player()
    pipeline._player = player  # type: ignore[assignment]
    pipeline._brain = _Brain()  # type: ignore[assignment]
    pipeline._latency_tracker = None

    async def _never_barge(**_kwargs) -> bool:
        await asyncio.sleep(3600)
        return False

    pipeline._barge_monitor = _never_barge  # type: ignore[assignment]
    await asyncio.wait_for(pipeline._brain_streaming("Wie spät ist es?", "de"), 5.0)
    return recorder, player


@pytest.mark.asyncio
async def test_all_sentences_failing_publishes_a_tts_error() -> None:
    recorder, player = await _run_turn(_BrokenTTS())

    assert player.played == 0
    tts_errors = [e for e in recorder.errors if e.layer == "speech.tts"]
    assert len(tts_errors) == 1
    assert "no API key" in tts_errors[0].message


@pytest.mark.asyncio
async def test_a_healthy_voice_publishes_nothing() -> None:
    recorder, player = await _run_turn(_HealthyTTS())

    assert player.played > 0
    assert [e for e in recorder.errors if e.layer == "speech.tts"] == []
