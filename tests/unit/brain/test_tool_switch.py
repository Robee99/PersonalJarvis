"""The tool switch: a few tools per turn, more switched on when the job needs them.

Live 2026-10-19 a 48-tool cap on the local brain hid every MCP tool, and
Roberto asked (2026-10-25) for a router that "only loads or fires em when
needed like a switch". Contract under test:

1. A turn opens with the core tools, the tools the request names, and a few
   siblings from the matched MCP server; everything else is hidden.
2. ``enable_tools`` switches more on, and the loop sends them the next round.
3. A hidden tool the model calls by its exact name is switched on and runs.
4. The manager puts local / small-window brains behind the switch and leaves
   a frontier cloud window alone, and ``off`` turns it off.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain.manager import BrainManager, _relevant_tool_names
from jarvis.brain.tool_switch import (
    ENABLE_TOOLS,
    EnableToolsTool,
    ToolSwitch,
    wants_switch,
)
from jarvis.brain.tool_use_loop import ToolUseLoop
from jarvis.core.protocols import BrainDelta, BrainRequest, ToolResult


@dataclass
class _Tool:
    name: str
    description: str = "does a thing"
    schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    is_mcp_tool: bool = False
    risk_tier: str = "safe"
    calls: int = 0

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        del args, ctx
        self.calls += 1
        return ToolResult(success=True, output=f"{self.name} done")


class _RunsTools:
    """Executor fake: runs the tool itself, no gates."""

    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        return await tool.execute(args, None)


def _registry() -> dict[str, _Tool]:
    tools: dict[str, _Tool] = {
        "navigate": _Tool("navigate", "Open a section of the Jarvis app"),
        "search_web": _Tool("search_web", "Search the web for current facts"),
    }
    for i in range(40):
        tools[f"setting_{i}"] = _Tool(f"setting_{i}", f"Change setting number {i}")
    for verb in ("navigate", "snapshot", "click", "type", "tabs", "close", "pdf", "drag"):
        name = f"playwright/browser_{verb}"
        tools[name] = _Tool(name, f"Browser {verb} in the web browser page", is_mcp_tool=True)
    tools["spotify"] = _Tool("spotify", "Play music, songs and playlists on Spotify")
    return tools


def _switch(tools: dict[str, Any]) -> ToolSwitch:
    switch = ToolSwitch(tools, _relevant_tool_names)
    tools[ENABLE_TOOLS] = EnableToolsTool(switch)
    return switch


def test_a_turn_opens_with_core_request_tools_and_server_siblings() -> None:
    tools = _registry()
    switch = _switch(tools)

    switch.open_turn("open youtube in the browser", core=("navigate", "search_web"))

    on = set(switch.visible())
    assert {ENABLE_TOOLS, "navigate", "search_web"} <= on
    assert "playwright/browser_navigate" in on
    # The click and snapshot the next step needs come along with the server.
    assert {"playwright/browser_click", "playwright/browser_snapshot"} <= on
    assert "spotify" not in on
    assert not any(name.startswith("setting_") for name in on)
    assert len(on) < len(tools) // 3


def test_a_web_job_gets_the_browser_hands_even_when_every_browser_name_matches() -> None:
    # Live 2026-10-25: "use the browser" switched on close/emulate_media/
    # evaluate, never navigate or snapshot, and Gemma fell back to computer_use.
    tools = _registry()
    for verb in ("emulate_media", "evaluate", "drop", "find", "fill_form", "wait_for"):
        name = f"playwright/browser_{verb}"
        tools[name] = _Tool(name, "Browser tool", is_mcp_tool=True)
    switch = _switch(tools)

    switch.open_turn("use the browser", core=())

    hands = {"navigate", "snapshot", "click", "type", "fill_form", "wait_for"}
    assert {f"playwright/browser_{v}" for v in hands} <= set(switch.visible())


def test_a_shopping_request_opens_the_browser_without_naming_it() -> None:
    tools = _registry()
    switch = _switch(tools)

    switch.open_turn("cheapest 1kg chicken across grocery stores", core=())

    assert {"playwright/browser_navigate", "playwright/browser_snapshot"} <= set(switch.visible())


def test_a_request_with_no_web_words_gets_no_browser() -> None:
    tools = _registry()
    switch = _switch(tools)

    switch.open_turn("turn the volume down", core=())

    assert not any(name.startswith("playwright/") for name in switch.visible())


@pytest.mark.asyncio
async def test_enable_tools_switches_on_what_the_job_describes() -> None:
    tools = _registry()
    switch = _switch(tools)
    switch.open_turn("hello", core=("navigate",))
    assert not switch.is_on("spotify")

    result = await tools[ENABLE_TOOLS].execute({"need": "play a song on spotify"}, None)

    assert result.success
    assert "spotify" in result.output
    assert switch.is_on("spotify")


@pytest.mark.asyncio
async def test_enable_tools_says_so_when_nothing_matches() -> None:
    tools = _registry()
    switch = _switch(tools)
    switch.open_turn("", core=())

    result = await tools[ENABLE_TOOLS].execute({"need": "zzqx"}, None)

    assert result.success
    assert "No further tool" in result.output


class _EnableThenPlayBrain:
    """Round 1 asks for music tools, round 2 plays, round 3 answers."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            call = {"id": "c1", "name": ENABLE_TOOLS, "input": {"need": "play music on spotify"}}
        elif len(self.requests) == 2:
            call = {"id": "c2", "name": "spotify", "input": {}}
        else:
            yield BrainDelta(content="Playing.")
            yield BrainDelta(finish_reason="stop")
            return
        yield BrainDelta(tool_call=call)
        yield BrainDelta(finish_reason="tool_use")


@pytest.mark.asyncio
async def test_the_loop_sends_switched_on_tools_from_the_next_round() -> None:
    tools = _registry()
    switch = _switch(tools)
    switch.open_turn("put something on", core=("navigate",))
    brain = _EnableThenPlayBrain()
    loop = ToolUseLoop(brain, tools, _RunsTools(), tool_switch=switch)  # type: ignore[arg-type]

    await loop.run([], user_utterance="put something on")

    first = {t["name"] for t in brain.requests[0].tools}
    second = {t["name"] for t in brain.requests[1].tools}
    assert "spotify" not in first
    assert "spotify" in second
    assert len(first) < len(tools)
    assert tools["spotify"].calls == 1


class _CallsHiddenToolBrain:
    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(tool_call={"id": "c1", "name": "spotify", "input": {}})
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="Done.")
        yield BrainDelta(finish_reason="stop")


@pytest.mark.asyncio
async def test_a_hidden_tool_called_by_name_is_switched_on_and_runs() -> None:
    tools = _registry()
    switch = _switch(tools)
    switch.open_turn("hello", core=("navigate",))
    brain = _CallsHiddenToolBrain()
    loop = ToolUseLoop(brain, tools, _RunsTools(), tool_switch=switch)  # type: ignore[arg-type]

    await loop.run([], user_utterance="play spotify")

    assert tools["spotify"].calls == 1
    assert switch.is_on("spotify")


def test_switch_mode_truth_table() -> None:
    big = 200
    assert wants_switch("auto", tool_count=big, context_window=65_536, local=True)
    assert wants_switch("auto", tool_count=big, context_window=0, local=True)
    assert not wants_switch("auto", tool_count=big, context_window=1_000_000, local=False)
    assert not wants_switch("auto", tool_count=10, context_window=65_536, local=True)
    assert wants_switch("on", tool_count=10, context_window=1_000_000, local=False)
    assert not wants_switch("off", tool_count=big, context_window=65_536, local=True)


class _LocalBrain:
    name = "local-openai"
    context_window = 65_536
    max_tools = 48
    supports_vision = True


class _CloudBrain:
    name = "claude-api"
    context_window = 1_000_000
    max_tools = 0
    supports_vision = True


def _manager(mode: str = "auto") -> BrainManager:
    m = BrainManager.__new__(BrainManager)  # bypass heavy __init__
    m._tools = {}
    m._evidence_required_tool = None
    m._skill_turn_match = None
    m._config = SimpleNamespace(brain=SimpleNamespace(tool_switch=mode, tool_switch_core=None))
    return m


def test_manager_puts_a_local_brain_behind_the_switch() -> None:
    opened = _manager()._open_tool_switch(_registry(), _LocalBrain(), "open the browser")

    assert opened is not None
    surface, switch = opened
    assert ENABLE_TOOLS in surface
    assert switch.is_on("navigate")
    assert switch.is_on("playwright/browser_navigate")
    assert not switch.is_on("spotify")


def test_manager_leaves_a_frontier_window_alone_and_honours_off() -> None:
    assert _manager()._open_tool_switch(_registry(), _CloudBrain(), "open the browser") is None
    assert _manager("off")._open_tool_switch(_registry(), _LocalBrain(), "browser") is None
