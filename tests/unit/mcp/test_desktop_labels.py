"""A desktop click that names its target in words lands on that target.

Live 2026-10-26 on Gemma 4 E4B: Windows-MCP's Click takes ``label`` as an
element number its Snapshot never prints, so "click on Roberto" was sent as
``label="Open Roberto profile"`` and refused six times. The worded label is
now read against the server's last Snapshot and sent as the point it names.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.core.events import ActionDenied, ActionExecuted
from jarvis.mcp import adapter as adapter_mod
from jarvis.mcp import desktop_labels
from jarvis.mcp.adapter import MCPToolAdapter
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import INVALID_ARGUMENTS_PREFIX, ToolExecutor

# The shape Windows-MCP 0.8.5 prints (tree/views.py _render_tree).
_SNAPSHOT = """Interactive Elements:
window "Google Chrome"
├── (812,430) button "Open Roberto profile"  [action: click]
├── (812,520) button "Open Guest profile"  [action: click]
└── (960,700) button "Add"  [action: click]

window "Settings"
├── (180,300) listitem "Display"  [action: click]
├── (640,610) listitem "Advanced display"  [action: click]
└── (640,690) listitem "Graphics"  [action: click]"""

# Windows-MCP 0.8.5's Click schema: loc is a list or a JSON string, label an integer.
_CLICK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "loc": {"anyOf": [{"type": "array", "items": {"type": "integer"}},
                          {"type": "string"}, {"type": "null"}]},
        "label": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "button": {"type": "string", "enum": ["left", "right", "middle"]},
        "clicks": {"type": "integer"},
    },
}


class _Client:
    def __init__(self, snapshot: str = _SNAPSHOT) -> None:
        self.spec = SimpleNamespace(name="windows-desktop")
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._snapshot = snapshot

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, dict(args)))
        if name == "Snapshot":
            return self._snapshot
        return f"Single left clicked at ({args['loc'][0]},{args['loc'][1]})."


def _tool(client: _Client, name: str, schema: dict[str, Any]) -> MCPToolAdapter:
    return MCPToolAdapter(client, {"name": name, "description": "", "inputSchema": schema})  # type: ignore[arg-type]


class _AutoApprove(ApprovalWorkflow):
    async def wait(self, trace_id: Any, timeout_s: float) -> tuple[bool, str]:  # type: ignore[override]
        return True, "user"


def _executor() -> ToolExecutor:
    bus = EventBus()

    async def _ignore(_event: Any) -> None:
        return None

    bus.subscribe(ActionDenied, _ignore)
    bus.subscribe(ActionExecuted, _ignore)
    return ToolExecutor(
        bus=bus, evaluator=RiskTierEvaluator(SafetyConfig()), approval=_AutoApprove(bus),
    )


def test_a_name_finds_its_element() -> None:
    assert desktop_labels.point_for("Open Roberto profile", _SNAPSHOT) == (812, 430)
    assert desktop_labels.point_for("Roberto", _SNAPSHOT) == (812, 430)
    assert desktop_labels.point_for("click on Advanced display", _SNAPSHOT) == (640, 610)


def test_an_exact_name_beats_a_longer_one() -> None:
    assert desktop_labels.point_for("Display", _SNAPSHOT) == (180, 300)


def test_an_ambiguous_or_missing_name_finds_nothing() -> None:
    assert desktop_labels.point_for("profile", _SNAPSHOT) is None
    assert desktop_labels.point_for("Windows Update", _SNAPSHOT) is None


def test_a_number_and_an_explicit_point_are_left_alone() -> None:
    assert desktop_labels.repair({"label": "7"}, _SNAPSHOT) == {"label": "7"}
    args = {"label": "Roberto", "loc": [1, 2]}
    assert desktop_labels.repair(args, _SNAPSHOT) == args


async def test_a_worded_click_after_a_snapshot_clicks_the_element(monkeypatch) -> None:
    monkeypatch.setattr(adapter_mod, "_LAST_SNAPSHOT", {})
    client = _Client()
    executor = _executor()
    await executor.execute(_tool(client, "Snapshot", {}), {}, trace_id=uuid4())

    result = await executor.execute(
        _tool(client, "Click", _CLICK_SCHEMA), {"label": "Open Roberto profile"}, trace_id=uuid4(),
    )

    assert result.success is True
    assert client.calls[-1] == ("Click", {"loc": [812, 430]})


async def test_a_worded_click_with_no_match_is_refused_with_the_way_to_call(monkeypatch) -> None:
    monkeypatch.setattr(adapter_mod, "_LAST_SNAPSHOT", {})
    client = _Client()
    executor = _executor()

    result = await executor.execute(
        _tool(client, "Click", _CLICK_SCHEMA), {"label": "Open Roberto profile"}, trace_id=uuid4(),
    )

    assert result.success is False
    assert result.error is not None and result.error.startswith(INVALID_ARGUMENTS_PREFIX)
    assert "loc=[x, y]" in result.output["message"]
    assert client.calls == []
