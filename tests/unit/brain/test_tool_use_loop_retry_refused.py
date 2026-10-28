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

    out = await loop.run(
        [BrainMessage(role="user", content="cheapest chicken")],
        reply_language="en",
    )

    assert executor.calls == 2
    assert out.text == "BigBasket is cheapest at Rs 240 a kilo."


async def test_the_nudge_is_given_only_once_per_turn() -> None:
    brain = _PromisingBrain()
    executor = _Executor(refusals=5)
    loop = ToolUseLoop(brain, {"search_web": _Tool()}, executor)

    out = await loop.run(
        [BrainMessage(role="user", content="cheapest chicken")],
        reply_language="en",
    )

    assert executor.calls == 2
    assert out.text == "That didn't work just now."


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


class _PromisesAgainAfterFailedRetryBrain:
    """Retries once, then still promises instead of making or reporting progress."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(tool_call={
                "id": "c1", "name": "search_web", "input": {"query": "x"}
            })
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="I will try the next site.")
        yield BrainDelta(finish_reason="stop")


async def test_second_promise_after_failed_tool_is_replaced_with_failure_report() -> None:
    brain = _PromisesAgainAfterFailedRetryBrain()
    executor = _FailingExecutor()
    loop = ToolUseLoop(brain, {"search_web": _Tool()}, executor)

    out = await loop.run(
        [BrainMessage(role="user", content="find the cheapest chicken")],
        reply_language="en",
    )

    assert executor.calls == 1
    assert len(brain.requests) == 3  # one tool round, one nudge, one final response
    assert out.text == "That didn't work just now."


class _MissingRefExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        self.calls += 1
        return ToolResult(
            success=False,
            output=None,
            error="click and type need a ref from the page, e.g. e12",
        )


class _RepeatingBadBrowserBrain:
    """Repeats a malformed BrowserSkill call with a fresh tool-call id."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if req.tools:
            yield BrainDelta(tool_call={
                "id": f"c{len(self.requests)}",
                "name": "my_chrome",
                "input": {"action": "type"},
            })
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="I could not continue: the browser control was unavailable.")
        yield BrainDelta(finish_reason="stop")


async def test_an_identical_failed_browser_call_is_suppressed_and_ends_cleanly() -> None:
    brain = _RepeatingBadBrowserBrain()
    executor = _MissingRefExecutor()
    loop = ToolUseLoop(brain, {"my_chrome": _Tool()}, executor)

    out = await loop.run([BrainMessage(role="user", content="type a search")])

    assert executor.calls == 1
    assert len(brain.requests) == 3
    assert brain.requests[-1].tools == ()
    assert "could not continue" in out.text


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
        # Live 2026-10-26, no call made.
        "Opening Windows PC settings, navigating to Display, and then selecting "
        "Advanced Display.",
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


@pytest.mark.parametrize(
    "first",
    [
        # Live 2026-10-26, both with no call made.
        "On it, I will check the current weather for Bengaluru.",
        "Noted, I will remind you to drink water in ten minutes.",
    ],
)
async def test_an_acknowledged_promise_is_sent_back(first: str) -> None:
    brain = _SaysThenCalls(first)
    executor = _Executor(refusals=0)
    loop = ToolUseLoop(brain, {"system_volume": _VolumeTool()}, executor)

    await loop.run([BrainMessage(role="user", content="weather in Bengaluru")])

    assert executor.calls == 1
    assert brain.requests[1].tool_choice == "required"


async def test_noted_with_remember_is_an_answer() -> None:
    brain = _SaysThenCalls("Noted, I will remember that you like BigBasket.")
    loop = ToolUseLoop(brain, {"system_volume": _VolumeTool()}, _Executor(refusals=0))

    await loop.run([BrainMessage(role="user", content="remember I like BigBasket")])

    assert len(brain.requests) == 1


async def test_a_mandated_tool_left_uncalled_is_forced_once() -> None:
    # Live 2026-10-26 a folder request carried the run_shell mandate and was
    # answered in words the announcement check did not catch.
    brain = _SaysThenCalls("The folder jarvis-b5 is ready in to-claude.")
    executor = _Executor(refusals=0)
    loop = ToolUseLoop(brain, {"system_volume": _VolumeTool()}, executor)

    await loop.run(
        [BrainMessage(role="user", content="create a folder named jarvis-b5")],
        evidence_required_tool="system_volume",
    )

    assert executor.calls == 1
    assert brain.requests[1].tool_choice == "required"


async def test_a_written_tool_marker_is_a_claimed_call_and_is_sent_back() -> None:
    # Live 2026-10-26 Gemma copied the history's tool marker instead of
    # calling: "[used tool app_ui_click]\nClicking on Roberto's profile now."
    brain = _SaysThenCalls("[used tool app_ui_click]\nRoberto's profile is open.")
    executor = _Executor(refusals=0)
    loop = ToolUseLoop(brain, {"system_volume": _VolumeTool()}, executor)

    out = await loop.run([BrainMessage(role="user", content="click on roberto")])

    assert executor.calls == 1
    assert brain.requests[1].tool_choice == "required"
    assert "[used tool" not in out.text


async def test_a_written_tool_marker_is_never_shown() -> None:
    brain = _SaysThenCalls("[used tool system_volume]\nThe volume is 35%.")
    loop = ToolUseLoop(brain, {}, _Executor(refusals=0))

    out = await loop.run([BrainMessage(role="user", content="volume?")])

    assert out.text == "The volume is 35%."


class _ScreenTool:
    name = "computer_use"
    schema: dict[str, Any] = {}
    description = "mouse and keyboard"


class _HiddenScreenSwitch:
    """A tool switch that left computer_use off for this request."""

    version = 0

    def __init__(self, tools: dict[str, Any]) -> None:
        self._tools = tools

    def visible(self) -> dict[str, Any]:
        return {k: v for k, v in self._tools.items() if k != "computer_use"}

    def switch_on(self, names: list[str]) -> bool:
        return False


async def test_the_retry_of_an_unmade_action_offers_screen_control() -> None:
    brain = _SaysThenCalls("Opening Windows PC settings, navigating to Display, and then "
                           "selecting Advanced Display.")
    tools = {"system_volume": _VolumeTool(), "computer_use": _ScreenTool()}
    loop = ToolUseLoop(brain, tools, _Executor(refusals=0))
    loop._switch = _HiddenScreenSwitch(tools)

    await loop.run([BrainMessage(role="user", content="open advanced display settings")])

    first, retry = brain.requests[0], brain.requests[1]
    assert "computer_use" not in [t["name"] for t in first.tools]
    assert "computer_use" in [t["name"] for t in retry.tools]


class _BrowserNavigate:
    name = "playwright/browser_navigate"
    schema: dict[str, Any] = {}
    description = "Navigate to a URL"


async def test_the_retry_of_a_web_job_keeps_to_the_browser_tools() -> None:
    # The switch leaves screen control off for a web job on purpose: with
    # both offered, Gemma clicked pixels on a page the browser could read.
    brain = _SaysThenCalls("Opening YouTube and playing I Mean It now.")
    tools = {
        "system_volume": _VolumeTool(),
        "computer_use": _ScreenTool(),
        "playwright/browser_navigate": _BrowserNavigate(),
    }
    loop = ToolUseLoop(brain, tools, _Executor(refusals=0))
    loop._switch = _HiddenScreenSwitch(tools)

    await loop.run([BrainMessage(role="user", content="go to youtube.com and play i mean it")])

    assert "computer_use" not in [t["name"] for t in brain.requests[1].tools]


async def test_the_retry_of_a_job_in_the_owners_open_browser_offers_screen_control() -> None:
    brain = _SaysThenCalls("Clicking on Roberto's profile now.")
    tools = {
        "system_volume": _VolumeTool(),
        "computer_use": _ScreenTool(),
        "playwright/browser_navigate": _BrowserNavigate(),
    }
    loop = ToolUseLoop(brain, tools, _Executor(refusals=0))
    loop._switch = _HiddenScreenSwitch(tools)

    await loop.run(
        [BrainMessage(role="user", content="chrome is already opened, click on roberto")]
    )

    assert "computer_use" in [t["name"] for t in brain.requests[1].tools]


@pytest.mark.parametrize(
    "first",
    [
        # Live 2026-10-26 on Gemma 4 E4B, each with no call made.
        "I have opened two tabs for you.",
        "I have zoomed in on the God's Eye View as requested. The screen now offers "
        "a more detailed view of the current global satellite data.",
        "I am navigating to the God's Eye View centered on New York.",
        "I've set an alarm for four minutes from now.",
    ],
)
async def test_a_reported_action_with_no_call_is_sent_back(first: str) -> None:
    brain = _SaysThenCalls(first)
    executor = _Executor(refusals=0)
    loop = ToolUseLoop(brain, {"system_volume": _VolumeTool()}, executor)

    await loop.run([BrainMessage(role="user", content="zoom in on god's eye view")])

    assert executor.calls == 1
    assert brain.requests[1].tool_choice == "required"


@pytest.mark.parametrize(
    "answer",
    [
        "I have not opened anything yet.",
        "Have I opened the right page?",
        "I am unable to see your hand without a camera.",
    ],
)
async def test_statements_that_report_no_action_are_not_sent_back(answer: str) -> None:
    brain = _SaysThenCalls(answer)
    loop = ToolUseLoop(brain, {"system_volume": _VolumeTool()}, _Executor(refusals=0))

    out = await loop.run([BrainMessage(role="user", content="what did you do")])

    assert len(brain.requests) == 1
    assert out.text == answer


class _KeepsClaiming:
    """Reports a finished action every round and never calls."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        yield BrainDelta(content="I have zoomed in on the God's Eye View.")
        yield BrainDelta(finish_reason="stop")


async def test_a_claim_repeated_after_the_retry_is_replaced_with_the_truth() -> None:
    brain = _KeepsClaiming()
    executor = _Executor(refusals=0)
    loop = ToolUseLoop(brain, {"system_volume": _VolumeTool()}, executor)

    out = await loop.run(
        [BrainMessage(role="user", content="zoom in on god's eye view")], reply_language="en"
    )

    assert executor.calls == 0
    assert len(brain.requests) == 2
    assert out.text.startswith("I did not start an action for that")


async def test_successful_action_is_recorded_without_optional_loop_controller():
    class PilotTool:
        name = "pc_pilot"
        schema = {"type": "object", "properties": {}}
        description = "Drive the desktop"

    class Brain:
        def __init__(self):
            self.calls = 0

        async def complete(self, req):
            self.calls += 1
            if self.calls == 1:
                yield BrainDelta(tool_call={"id": "scroll1", "name": "pc_pilot", "input": {}})
                yield BrainDelta(finish_reason="tool_use")
            else:
                yield BrainDelta(content="Scrolled down in Settings.")
                yield BrainDelta(finish_reason="stop")

    brain = Brain()
    loop = ToolUseLoop(brain, {"pc_pilot": PilotTool()}, _Executor(refusals=0))
    result = await loop.run([BrainMessage(role="user", content="scroll down in Settings")])
    assert brain.calls == 2
    assert result.text == "Scrolled down in Settings."
