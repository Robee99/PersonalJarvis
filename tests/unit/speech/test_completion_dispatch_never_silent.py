"""The buffered-completion dispatch must end every turn audibly (AD-OE6).

``_handle_flushed_pending_text`` sends a held fragment to the brain once the
grace window expires. It used to await a non-streaming brain with no bound at
all, and on the streaming side it dropped both a stall and an empty answer
without a word. The user had finished speaking, so Jarvis just went quiet while
the session stayed open. These tests pin the same endings the primary dispatch
path already has: a timeout notice for a stall, and the silent-turn handler
(which speaks e.g. "brain unreachable") for an empty answer.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.speech.pipeline import SpeechPipeline, TurnTakingState


class _HangingBrain:
    async def generate(self, text: str) -> str:
        await asyncio.sleep(3600)
        return "never"


class _EmptyFailedBrain:
    """A brain whose whole provider chain failed: empty text, flag set."""

    _last_turn_all_failed = True

    async def generate(self, text: str) -> str:
        return ""


def _make_pipe(brain: object, *, streaming: bool) -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._brain = brain
    pipe._config = SimpleNamespace(voice=SimpleNamespace(clarify_incomplete_enabled=False))
    pipe._brain_hard_timeout_s = 0.05
    pipe._assistant_work_count = 0
    pipe.events: list[str] = []

    async def _set_turn_state(state: TurnTakingState, **_kw: object) -> None:
        pipe._turn_state = state

    async def _speak_brain_timeout(lang: str, *, site: str = "") -> None:
        pipe.events.append(f"timeout:{site}")

    async def _speak_brain_unavailable(lang: str) -> None:
        pipe.events.append("unavailable")

    async def _stalling_stream(text: str, lang: str) -> tuple[str, bool]:
        raise AssertionError("replaced by the stall guard fake")

    async def _stall_guard(coro, **_kw: object) -> tuple[str, bool]:
        coro.close()
        raise TimeoutError

    pipe._set_turn_state = _set_turn_state  # type: ignore[method-assign]
    pipe._speak_brain_timeout = _speak_brain_timeout  # type: ignore[method-assign]
    pipe._speak_brain_unavailable = _speak_brain_unavailable  # type: ignore[method-assign]
    pipe._streaming_enabled = lambda: streaming  # type: ignore[method-assign]
    pipe._brain_streaming = _stalling_stream  # type: ignore[method-assign]
    pipe._run_brain_with_stall_guard = _stall_guard  # type: ignore[method-assign]
    pipe._spoke_this_turn = False
    return pipe


@pytest.mark.asyncio
async def test_hanging_nonstreaming_brain_speaks_the_timeout_notice() -> None:
    pipe = _make_pipe(_HangingBrain(), streaming=False)

    await asyncio.wait_for(pipe._handle_flushed_pending_text("Wie spät ist es", "de"), 2.0)

    assert pipe.events == ["timeout:completion_nonstream_cap"]
    assert pipe._turn_state is TurnTakingState.LISTENING
    assert pipe._assistant_work_count == 0


@pytest.mark.asyncio
async def test_empty_answer_from_a_failed_brain_is_spoken() -> None:
    pipe = _make_pipe(_EmptyFailedBrain(), streaming=False)

    await pipe._handle_flushed_pending_text("Wie spät ist es", "de")

    assert pipe.events == ["unavailable"]


@pytest.mark.asyncio
async def test_streaming_stall_speaks_the_timeout_notice() -> None:
    pipe = _make_pipe(_HangingBrain(), streaming=True)

    await pipe._handle_flushed_pending_text("Wie spät ist es", "de")

    assert pipe.events == ["timeout:completion_stall"]
    assert pipe._turn_state is TurnTakingState.LISTENING
