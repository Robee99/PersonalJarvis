"""Queue-driven fake of the ``google.genai`` Live ``AsyncSession`` wire.

The real adapter (``jarvis.plugins.realtime.gemini_live._GeminiLiveSession``)
runs on top of it unchanged, so a test drives the exact message shapes the
server sends (audio, transcripts, ``interrupted``, empty ``turn_complete``) and
sees what the adapter and the realtime session make of them. Like the SDK,
``receive()`` ends after one model turn; the adapter re-enters it.

Per AGENTS.md: a real fake, never ``unittest.mock``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

#: 0.1 s of 24 kHz 16-bit mono PCM.
PCM_100MS = b"\x01\x02" * 2400


def _content(
    *,
    output_text: str | None = None,
    input_text: str | None = None,
    interrupted: bool = False,
    turn_complete: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        output_transcription=(
            SimpleNamespace(text=output_text) if output_text is not None else None
        ),
        input_transcription=(
            SimpleNamespace(text=input_text) if input_text is not None else None
        ),
        interrupted=interrupted,
        turn_complete=turn_complete,
        turn_complete_reason=None,
    )


def _message(*, data: bytes | None = None, content: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        server_content=content,
        tool_call=None,
        go_away=None,
        usage_metadata=None,
    )


def user_said(text: str) -> SimpleNamespace:
    """The server's input transcription of the user's words."""
    return _message(content=_content(input_text=text))


def model_spoke(text: str) -> list[SimpleNamespace]:
    """One spoken chunk: PCM plus its output transcription."""
    return [
        _message(data=PCM_100MS),
        _message(content=_content(output_text=text)),
    ]


def interrupted() -> SimpleNamespace:
    return _message(content=_content(interrupted=True))


def turn_complete() -> SimpleNamespace:
    """A model-turn boundary; with nothing before it, an EMPTY turn."""
    return _message(content=_content(turn_complete=True))


class FakeGeminiLiveSdk:
    """Messages are fed by the test; text inputs are recorded."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[SimpleNamespace] = asyncio.Queue()
        self.text_inputs: list[str] = []
        self.audio_inputs = 0

    def feed(self, *messages: SimpleNamespace) -> None:
        for message in messages:
            self._queue.put_nowait(message)

    async def receive(self) -> AsyncIterator[SimpleNamespace]:
        while True:
            message = await self._queue.get()
            yield message
            content = getattr(message, "server_content", None)
            if content is not None and getattr(content, "turn_complete", False):
                return

    async def send_realtime_input(
        self, *, audio: Any = None, text: str | None = None, **_: Any
    ) -> None:
        if text:
            self.text_inputs.append(text)
        if audio is not None:
            self.audio_inputs += 1

    async def send_tool_response(self, **_: Any) -> None:
        return None

    async def close(self) -> None:
        return None
