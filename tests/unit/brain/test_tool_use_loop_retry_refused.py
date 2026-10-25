"""A refused call answered with words goes back to the tool once.

Live 2026-10-25 on Gemma 4 E4B: a search call was refused for its arguments,
the model replied "I will try again" without a call, and the turn ended on
that promise. Contract: one nudge per turn, only after a schema refusal.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from jarvis.brain.tool_use_loop import ToolUseLoop
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest, ToolResult
from jarvis.safety.tool_executor import INVALID_ARGUMENTS_PREFIX


class _Tool:
    name = "search_web"
    schema: dict[str, Any] = {}
    description = "search"


class _Executor:
    """Refuses the first call for its arguments, answers the rest."""

    def __init__(self, refusals: int = 1) -> None:
        self.calls = 0
        self._refusals = refusals

    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        self.calls += 1
        if self.calls <= self._refusals:
            return ToolResult(
                success=False, output=None, error=f"{INVALID_ARGUMENTS_PREFIX}: missing 'query'"
            )
        return ToolResult(success=True, output="BigBasket Rs 240/kg")


class _PromisingBrain:
    """Calls, promises a retry in words, and calls again only when told to."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        last = str(req.messages[-1].content)
        if len(self.requests) == 1 or last.startswith("[retry]"):
            n = len(self.requests)
            call = {"id": f"c{n}", "name": "search_web", "input": {"query": "x"}}
            yield BrainDelta(tool_call=call)
            yield BrainDelta(finish_reason="tool_use")
            return
        result = req.messages[-1]
        if result.role == "tool" and INVALID_ARGUMENTS_PREFIX not in str(result.content):
            yield BrainDelta(content="BigBasket is cheapest at Rs 240 a kilo.")
        else:
            yield BrainDelta(content="The search failed. I will try again.")
        yield BrainDelta(finish_reason="stop")


async def test_a_promise_after_a_refusal_is_sent_back_to_the_tool_once() -> None:
    brain = _PromisingBrain()
    executor = _Executor()
    loop = ToolUseLoop(brain, {"search_web": _Tool()}, executor)

    out = await loop.run([BrainMessage(role="user", content="cheapest chicken")])

    assert executor.calls == 2
    assert out.text == "BigBasket is cheapest at Rs 240 a kilo."


async def test_the_nudge_is_given_only_once_per_turn() -> None:
    brain = _PromisingBrain()
    executor = _Executor(refusals=5)
    loop = ToolUseLoop(brain, {"search_web": _Tool()}, executor)

    out = await loop.run([BrainMessage(role="user", content="cheapest chicken")])

    assert executor.calls == 2
    assert "try again" in out.text
