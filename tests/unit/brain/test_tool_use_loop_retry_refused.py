"""A refused call answered with words goes back to the tool once.

Live 2026-10-25 on Gemma 4 E4B: a search call was refused for its arguments,
the model replied "I will try again" without a call, and the turn ended on
that promise. Contract: one nudge per turn, after a schema refusal or after
a failed call the model answers with a promise instead of a call.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

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


class _FailingExecutor:
    """The first call runs and fails (no such element); the rest succeed."""

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        self.calls += 1
        if self.calls == 1:
            return ToolResult(success=False, output=None, error='"Search bar" does not match')
        return ToolResult(success=True, output="BigBasket Rs 240/kg")


class _NextSiteBrain(_PromisingBrain):
    """After a failed call it promises the next step; after a result it answers."""

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        last = req.messages[-1]
        if len(self.requests) == 1 or str(last.content).startswith("[retry]"):
            n = len(self.requests)
            yield BrainDelta(tool_call={"id": f"c{n}", "name": "search_web", "input": {}})
            yield BrainDelta(finish_reason="tool_use")
            return
        if "Rs 240" in str(last.content):
            yield BrainDelta(content="BigBasket is cheapest at Rs 240 a kilo.")
        else:
            yield BrainDelta(content="I cannot find the search bar. I will try the next site.")
        yield BrainDelta(finish_reason="stop")


async def test_a_failed_call_answered_with_a_promise_is_sent_back_once() -> None:
    brain = _NextSiteBrain()
    executor = _FailingExecutor()
    loop = ToolUseLoop(brain, {"search_web": _Tool()}, executor)

    out = await loop.run([BrainMessage(role="user", content="cheapest chicken")])

    assert executor.calls == 2
    assert out.text == "BigBasket is cheapest at Rs 240 a kilo."


class _HonestBrain(_PromisingBrain):
    """Says plainly that it could not do it, with no promise."""

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(tool_call={"id": "c1", "name": "search_web", "input": {}})
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="The store page had no search box, so I could not check it.")
        yield BrainDelta(finish_reason="stop")


async def test_a_failed_call_reported_plainly_ends_the_turn() -> None:
    executor = _FailingExecutor()
    loop = ToolUseLoop(_HonestBrain(), {"search_web": _Tool()}, executor)

    out = await loop.run([BrainMessage(role="user", content="cheapest chicken")])

    assert executor.calls == 1
    assert "could not check" in out.text


class _VolumeTool:
    name = "system_volume"
    schema: dict[str, Any] = {}
    description = "volume"


class _AnnouncingBrain:
    """Says it is setting the volume, and calls the tool only when told no tool ran."""

    def __init__(self, *, chat: bool = False) -> None:
        self.requests: list[BrainRequest] = []
        self._chat = chat

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        last = req.messages[-1]
        if last.role == "tool":
            yield BrainDelta(content="The volume is 35%.")
        elif str(last.content).startswith("[retry]"):
            if self._chat:
                yield BrainDelta(content="Good evening, Sir.")
            else:
                call = {"id": "v1", "name": "system_volume", "input": {"action": "set"}}
                yield BrainDelta(tool_call=call)
                yield BrainDelta(finish_reason="tool_use")
                return
        else:
            yield BrainDelta(content="Setting the volume to 35 percent now.")
        yield BrainDelta(finish_reason="stop")


async def test_an_action_announced_with_no_call_is_sent_back_once() -> None:
    brain = _AnnouncingBrain()
    executor = _Executor(refusals=0)
    loop = ToolUseLoop(brain, {"system_volume": _VolumeTool()}, executor)

    out = await loop.run([BrainMessage(role="user", content="set my volume to 35")])

    assert executor.calls == 1
    assert out.text == "The volume is 35%."


class _GreetingBrain:
    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        yield BrainDelta(content="Good evening, Sir. Let me know if you need anything.")
        yield BrainDelta(finish_reason="stop")


async def test_a_plain_answer_with_no_call_is_not_sent_back() -> None:
    brain = _GreetingBrain()
    loop = ToolUseLoop(brain, {"system_volume": _VolumeTool()}, _Executor(refusals=0))

    out = await loop.run([BrainMessage(role="user", content="hello")])

    assert len(brain.requests) == 1
    assert out.text.startswith("Good evening")


async def test_a_turn_that_needs_no_action_answers_again_without_the_promise() -> None:
    brain = _AnnouncingBrain(chat=True)
    executor = _Executor(refusals=0)
    loop = ToolUseLoop(brain, {"system_volume": _VolumeTool()}, executor)

    out = await loop.run([BrainMessage(role="user", content="hello")])

    assert executor.calls == 0
    assert out.text == "Good evening, Sir."


async def test_the_retry_after_an_announced_action_must_be_a_tool_call() -> None:
    # Live 2026-10-25 Nemotron 3.5 Bolt E4B said "setting the volume" six
    # times with no call; the retry asks the server for a call, not words.
    brain = _AnnouncingBrain()
    loop = ToolUseLoop(brain, {"system_volume": _VolumeTool()}, _Executor(refusals=0))

    await loop.run([BrainMessage(role="user", content="set my volume to 35")])

    assert [r.tool_choice for r in brain.requests] == [None, "required", None]


async def test_the_retry_after_a_refusal_leaves_the_choice_to_the_model() -> None:
    brain = _PromisingBrain()
    loop = ToolUseLoop(brain, {"search_web": _Tool()}, _Executor())

    await loop.run([BrainMessage(role="user", content="cheapest chicken")])

    assert all(r.tool_choice is None for r in brain.requests)


class _SaysThenCalls:
    """Answers ``first`` with no call, then calls the browser tool when told no tool ran."""

    def __init__(self, first: str) -> None:
        self.requests: list[BrainRequest] = []
        self._first = first

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        last = req.messages[-1]
        if last.role == "tool":
            yield BrainDelta(content="YouTube is open.")
        elif str(last.content).startswith("[retry]"):
            call = {"id": "b1", "name": "system_volume", "input": {"action": "get"}}
            yield BrainDelta(tool_call=call)
            yield BrainDelta(finish_reason="tool_use")
            return
        else:
            yield BrainDelta(content=self._first)
        yield BrainDelta(finish_reason="stop")


@pytest.mark.parametrize(
    "first",
    [
        # Live 2026-10-25 on the installed build, both with no call made.
        "Opening youtube.com now.",
        "Opening the specified page in the browser and filling the form fields.",
    ],
)
async def test_announced_browser_actions_are_sent_back(first: str) -> None:
    brain = _SaysThenCalls(first)
    executor = _Executor(refusals=0)
    loop = ToolUseLoop(brain, {"system_volume": _VolumeTool()}, executor)

    await loop.run([BrainMessage(role="user", content="open youtube.com in the browser")])

    assert executor.calls == 1
    assert brain.requests[1].tool_choice == "required"


@pytest.mark.parametrize(
    "answer",
    [
        "Playing music helps many people focus.",
        "The weather in Bengaluru today is overcast.",
        "I am Jarvis.",
    ],
)
async def test_plain_statements_are_not_sent_back(answer: str) -> None:
    brain = _SaysThenCalls(answer)
    loop = ToolUseLoop(brain, {"system_volume": _VolumeTool()}, _Executor(refusals=0))

    out = await loop.run([BrainMessage(role="user", content="tell me something")])

    assert len(brain.requests) == 1
    assert out.text == answer


async def test_the_retry_names_the_request_it_is_about() -> None:
    # Live 2026-10-25 Gemma answered a bare "[retry]" line with "I see you've
    # provided some context, but I do not have a specific question."
    brain = _AnnouncingBrain()
    loop = ToolUseLoop(brain, {"system_volume": _VolumeTool()}, _Executor(refusals=0))

    await loop.run([BrainMessage(role="user", content="set my volume to 35")])

    retry = str(brain.requests[1].messages[-1].content)
    assert retry.startswith("[retry]")
    assert retry.endswith("The request was: set my volume to 35")


class _CameraTool:
    name = "find_cctv_cameras"
    schema: dict[str, Any] = {}
    description = "cameras in an area"


class _NarrowingBrain:
    """Asks for a narrower area first; calls with the city when told it is enough."""

    def __init__(self, first: str) -> None:
        self.requests: list[BrainRequest] = []
        self._first = first

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        last = req.messages[-1]
        if last.role == "tool":
            yield BrainDelta(content="The catalog has no public cameras in Mumbai.")
        elif str(last.content).startswith("[retry]"):
            call = {"id": "g1", "name": "find_cctv_cameras", "input": {"area": {"place": "Mumbai"}}}
            yield BrainDelta(tool_call=call)
            yield BrainDelta(finish_reason="tool_use")
            return
        else:
            yield BrainDelta(content=self._first)
        yield BrainDelta(finish_reason="stop")


async def test_a_request_to_narrow_down_a_named_place_is_sent_back_once() -> None:
    brain = _NarrowingBrain(
        "I can search for public CCTV cameras in Mumbai, but I need to know the area "
        "you are interested in more specifically, such as a particular neighborhood "
        "or a bounding box, to give you the most relevant results."
    )
    executor = _Executor(refusals=0)
    loop = ToolUseLoop(brain, {"find_cctv_cameras": _CameraTool()}, executor)

    out = await loop.run(
        [BrainMessage(role="user", content="Find public CCTV cameras in Mumbai.")]
    )

    assert executor.calls == 1
    assert out.text == "The catalog has no public cameras in Mumbai."
    retry = str(brain.requests[1].messages[-1].content)
    assert "Do not ask the person to be more specific" in retry
    assert brain.requests[1].tool_choice is None


async def test_a_question_for_a_missing_value_is_not_sent_back() -> None:
    brain = _NarrowingBrain("Which city should I look in?")
    loop = ToolUseLoop(brain, {"find_cctv_cameras": _CameraTool()}, _Executor(refusals=0))

    out = await loop.run([BrainMessage(role="user", content="Find public CCTV cameras.")])

    assert len(brain.requests) == 1
    assert out.text == "Which city should I look in?"
