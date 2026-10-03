"""A turn torn down while the sentence queue is full must still unwind.

``_brain_streaming`` bounds look-ahead with ``sentence_channels`` (maxsize =
``tts_lookahead_sentences``, default 1). A long streamed answer fills it, so the
producer sits blocked on ``put``. When the user barges in or hangs up, the
teardown cancels the producer and the playback consumer together. The
producer's ``finally`` then used to ``await put(None)`` for its end sentinel on
that still-full queue, whose only consumer had just been cancelled. That put
can never return, so the teardown's ``await produce_task`` hung and the
interrupted turn stayed wedged until the outer 30 s stall guard fired, which
also dropped what the user had just said.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline


@dataclass
class _EchoTTS:
    name: str = "echo-tts"
    supports_streaming: bool = True

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        yield AudioChunk(
            pcm=text.encode("utf-8"), sample_rate=24_000, timestamp_ns=0, channels=1,
        )


@dataclass
class _HoldingPlayer:
    """Starts the first sentence and then holds, like a long sentence playing."""

    stop_calls: int = 0
    play_started: asyncio.Event = field(default_factory=asyncio.Event)

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        async for _chunk in chunks:
            self.play_started.set()
            await asyncio.sleep(3600)

    def stop(self) -> None:
        self.stop_calls += 1


class _LongAnswerBrain:
    """Streams more sentences than the look-ahead queue can hold."""

    def __init__(self) -> None:
        self.producer_blocked = asyncio.Event()

    async def generate_stream(self, text: str) -> AsyncIterator[str]:
        for i in range(6):
            yield f"Satz Nummer {i} ist hier. "
        # Reached only if the queue never filled up.
        self.producer_blocked.set()


@pytest.mark.asyncio
async def test_hangup_with_full_sentence_queue_unwinds_promptly() -> None:
    player = _HoldingPlayer()
    brain = _LongAnswerBrain()
    pipeline = SpeechPipeline(tts=_EchoTTS(), bus=EventBus(), enable_whisper_wake=False)
    pipeline._player = player  # type: ignore[assignment]
    pipeline._brain = brain  # type: ignore[assignment]
    pipeline._latency_tracker = None
    pipeline._tts_lookahead_sentences = 1

    async def _never_barge(**_kwargs) -> bool:
        await asyncio.sleep(3600)
        return False

    pipeline._barge_monitor = _never_barge  # type: ignore[assignment]

    turn = asyncio.create_task(pipeline._brain_streaming("Erzähl was Langes.", "de"))
    await asyncio.wait_for(player.play_started.wait(), timeout=2.0)
    # Let the producer run until it blocks on the full queue.
    for _ in range(20):
        await asyncio.sleep(0)
    assert not brain.producer_blocked.is_set(), "the queue must be full for this test"

    started = time.monotonic()
    pipeline._hangup_event.set()
    _text, barged = await asyncio.wait_for(turn, timeout=10.0)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5, f"teardown took {elapsed:.2f}s"

    assert barged is True
    assert player.stop_calls >= 1
