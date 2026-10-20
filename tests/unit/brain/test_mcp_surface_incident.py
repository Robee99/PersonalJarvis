"""Regression coverage for the observed connected-MCP routing incident."""
import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.brain.tool_use_loop import ToolUseLoop
from jarvis.core.protocols import BrainDelta, BrainRequest, ToolResult


@dataclass
class Tool:
    name: str
    description: str = "Change an application setting."
    schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    is_mcp_tool: bool = False


def fitted_tools(query: str) -> dict[str, Tool]:
    manager = BrainManager.__new__(BrainManager)
    manager._evidence_required_tool = None
    manager._skill_turn_match = None
    tools = {f"setting_{i}": Tool(f"setting_{i}") for i in range(90)}
    for i in range(35):
        name = f"world/panel_{i}"
        tools[name] = Tool(name, "Inspect the world panel data.", is_mcp_tool=True)
    tools["gods-eye-view/show_in_gods_eye_view"] = Tool(
        "gods-eye-view/show_in_gods_eye_view",
        "Shows a view in God's Eye View: the live globe. An area framed from above "
        "or a camera position, with chosen data layers, visual style and map.",
        is_mcp_tool=True,
    )
    tools["windows-desktop/App"] = Tool(
        "windows-desktop/App", "Open/start/launch applications and manage windows.",
        is_mcp_tool=True,
    )
    tools["tools-list"] = Tool(
        "tools-list", "Return the effective live Brain tool surface, including native, "
        "connected CLI, Marketplace, and MCP tools.",
    )
    brain = type("Brain", (), {"context_window": 65536, "max_tools": 48})()
    return manager._fit_tools_to_brain(tools, brain, system_prompt="", history=None, query=query)


@pytest.mark.parametrize("query, expected", [
    ("can u fly me to new york in the world panel", "gods-eye-view/show_in_gods_eye_view"),
    ("can u open chrome", "windows-desktop/App"),
    ("hi", "tools-list"),
    ("list mcps available to U?", "tools-list"),
])
def test_connected_tools_survive_natural_user_wording(query: str, expected: str) -> None:
    fitted = fitted_tools(query)
    assert expected in fitted
    assert len(fitted) <= 48


class MarkerBrain:
    def __init__(self, recover: bool, delay: float = 0) -> None:
        self.requests: list[BrainRequest] = []
        self.recover = recover
        self.delay = delay

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        await asyncio.sleep(self.delay)
        if self.recover and len(self.requests) == 2:
            yield BrainDelta(tool_call={"id": "actual", "name": "windows-desktop/App",
                                        "input": {"mode": "launch", "name": "Notepad"}})
            yield BrainDelta(finish_reason="tool_use")
        else:
            text = ("The application launch returned a genuine result."
                    if len(self.requests) > 2 else (
                "[used tool windows-desktop_App] I've opened it."
            ))
            yield BrainDelta(content=text)
            yield BrainDelta(finish_reason="stop")


class Executor:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, tool: Any, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        self.calls.append(args)
        return ToolResult(success=True, output="Synthetic launch receipt")


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [False, True])
async def test_history_use_marker_is_not_an_execution_receipt(recover: bool) -> None:
    brain = MarkerBrain(recover)
    executor = Executor()
    loop = ToolUseLoop(brain, {"windows-desktop/App": Tool("windows-desktop/App")}, executor)
    spoken: list[str] = []
    result = await loop.run([], user_utterance="open Notepad", reply_language="en",
                            text_consumer=spoken.append)
    assert "[used tool" not in "".join(spoken)
    assert "[used tool" not in result.text
    if recover:
        assert len(executor.calls) == 1
        assert result.executed_tool_names == {"windows-desktop/App"}
    else:
        assert not executor.calls
        assert result.finish_reason == "error"
        assert len(brain.requests) == 2

def test_discovery_does_not_exceed_a_protected_single_tool_cap() -> None:
    manager = BrainManager.__new__(BrainManager)
    manager._evidence_required_tool = None
    manager._skill_turn_match = None
    tools = {name: Tool(name) for name in ["computer_use", "tools-list"]}
    brain = type("Brain", (), {"context_window": 65536, "max_tools": 1})()
    fitted = manager._fit_tools_to_brain(tools, brain, system_prompt="", history=None)
    assert set(fitted) == {"computer_use"}


@pytest.mark.asyncio
async def test_marker_recovery_does_not_restart_after_deadline() -> None:
    brain = MarkerBrain(True, delay=0.02)
    executor = Executor()
    loop = ToolUseLoop(brain, {"windows-desktop/App": Tool("windows-desktop/App")},
                       executor, deadline_s=0.001)
    result = await loop.run([], user_utterance="open Notepad", reply_language="en")
    assert len(brain.requests) == 1
    assert not executor.calls
    assert result.finish_reason == "error"


@pytest.mark.asyncio
async def test_marker_fallback_survives_a_closed_consumer() -> None:
    def closed(text: str) -> None:
        raise RuntimeError("synthetic closed consumer")

    loop = ToolUseLoop(MarkerBrain(False),
                       {"windows-desktop/App": Tool("windows-desktop/App")}, Executor())
    result = await loop.run([], user_utterance="open Notepad", reply_language="en",
                            text_consumer=closed)
    assert result.finish_reason == "error"
