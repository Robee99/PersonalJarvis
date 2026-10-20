"""The ``app_ui_*`` router tools: read and use the open Jarvis screen.

Each tool is one ``AppUiRequest`` round trip; ``FakeAppWindowBus`` plays the
main window. The last tests prove the tools are wired end to end: factory
entry points, the router set, and the Jarvis MCP catalog Hermes reads.
"""

from __future__ import annotations

from typing import Any

import mcp.types as types
import pytest

from jarvis.core.app_ui_bridge import AppUiBridge
from jarvis.core.events import AppUiRequest
from jarvis.plugins.tool.app_ui import (
    LABEL_WAIT_MS,
    AppUiClickTool,
    AppUiScrollTool,
    AppUiSnapshotTool,
    AppUiTypeTool,
    render_snapshot,
)
from tests.fakes.fake_app_window import FakeAppWindowBus

SNAPSHOT = {
    "section": "settings",
    "section_label": "Settings",
    "headings": ["Settings"],
    "scroll": "top, more below",
    "total": 130,
    "elements": [
        {"ref": "e1", "role": "button", "name": "Confirm", "region": "dialog"},
        {"ref": "e2", "role": "tab", "name": "General", "region": "main", "selected": True},
        {"ref": "e3", "role": "tab", "name": "Model", "region": "main"},
        {"ref": "e4", "role": "textbox", "name": "API name", "region": "main", "value": "abc"},
        {
            "ref": "e5",
            "role": "switch",
            "name": "Autopilot toasts",
            "region": "main",
            "checked": True,
        },
        {
            "ref": "e6",
            "role": "button",
            "name": "Delete all",
            "region": "main",
            "destructive": True,
        },
        {"ref": "e7", "role": "button", "name": "Chats", "region": "nav"},
    ],
}


def test_snapshot_text_is_compact_and_grouped() -> None:
    text = render_snapshot(SNAPSHOT)
    lines = text.splitlines()

    assert lines[0] == "Screen: Settings — Settings"
    assert "Scroll: top, more below" in lines
    assert lines.index("Open dialog (deal with it first):") < lines.index("Main content:")
    assert lines.index("Main content:") < lines.index("Navigation (sidebar and tab bars):")
    assert 'e2 tab "General" selected' in lines
    assert 'e4 textbox "API name" = "abc"' in lines
    assert 'e5 switch "Autopilot toasts" on' in lines
    assert 'e6 button "Delete all" destructive: user only' in lines
    assert "(123 more controls not listed; scroll to reach them.)" in lines


async def test_snapshot_tool_returns_the_rendered_screen() -> None:
    bus = FakeAppWindowBus({"ok": True, "result": SNAPSHOT})
    result = await AppUiSnapshotTool(bus).execute({}, ctx=None)

    assert result.success is True
    assert result.output == render_snapshot(SNAPSHOT)
    assert bus.requests[0].action == "snapshot"


async def test_click_by_label_waits_for_render_and_reports_the_new_screen() -> None:
    bus = FakeAppWindowBus(
        {
            "ok": True,
            "result": {
                "clicked": {"name": "Model", "role": "tab", "region": "main"},
                "section_label": "Settings",
                "headings": ["Settings", "Model"],
                "count": 34,
            },
        }
    )
    result = await AppUiClickTool(bus).execute({"label": "  Model "}, ctx=None)

    assert result.success is True
    assert (
        result.output == 'Clicked tab "Model". Screen: Settings — Settings › Model (34 controls).'
    )
    request = bus.requests[0]
    assert (request.action, request.label, request.ref) == ("click", "Model", "")
    assert request.wait_ms == LABEL_WAIT_MS


async def test_click_by_ref_does_not_wait() -> None:
    bus = FakeAppWindowBus({"ok": True, "result": {"clicked": {"name": "Model", "role": "tab"}}})
    await AppUiClickTool(bus).execute({"ref": "e3"}, ctx=None)

    assert (bus.requests[0].ref, bus.requests[0].wait_ms) == ("e3", 0)


async def test_click_refusal_is_the_tool_error() -> None:
    error = '"Delete all" is a destructive control. The user has to click it themselves.'
    bus = FakeAppWindowBus({"ok": False, "error": error})
    result = await AppUiClickTool(bus).execute({"ref": "e6"}, ctx=None)

    assert result.success is False
    assert result.error == error


async def test_click_needs_a_target() -> None:
    bus = FakeAppWindowBus()
    result = await AppUiClickTool(bus).execute({}, ctx=None)

    assert result.success is False
    assert bus.events == []


async def test_type_passes_text_and_submit() -> None:
    bus = FakeAppWindowBus(
        {"ok": True, "result": {"typed_into": {"name": "Search"}, "submitted": True}}
    )
    result = await AppUiTypeTool(bus).execute(
        {"label": "Search", "text": "weather", "submit": True}, ctx=None
    )

    assert result.success is True
    assert result.output.startswith('Typed into "Search" and pressed Enter.')
    request = bus.requests[0]
    assert (request.action, request.text, request.submit) == ("type", "weather", True)


async def test_scroll_validates_direction_and_reports_position() -> None:
    bus = FakeAppWindowBus({"ok": True, "result": {"scrolled": True, "position": "bottom"}})
    tool = AppUiScrollTool(bus)

    bad = await tool.execute({"direction": "sideways"}, ctx=None)
    assert bad.success is False and bus.events == []

    good = await tool.execute({"direction": "down"}, ctx=None)
    assert good.success is True and good.output == "Scrolled down; now at bottom."
    assert bus.requests[0].direction == "down"


async def test_no_window_is_said_plainly() -> None:
    bus = FakeAppWindowBus(answer=False)
    tool = AppUiSnapshotTool(bus, bridge=AppUiBridge(bus, timeout_s=0.05))
    result = await tool.execute({}, ctx=None)

    assert result.success is False
    assert "window did not answer" in (result.error or "")


def test_every_tool_is_safe_and_named_for_mcp() -> None:
    names = {
        cls.name for cls in (AppUiSnapshotTool, AppUiClickTool, AppUiTypeTool, AppUiScrollTool)
    }
    assert names == {"app_ui_snapshot", "app_ui_click", "app_ui_type", "app_ui_scroll"}
    for cls in (AppUiSnapshotTool, AppUiClickTool, AppUiTypeTool, AppUiScrollTool):
        assert cls.risk_tier == "safe"


def test_router_set_and_deny_lists_name_them() -> None:
    from jarvis.brain.factory import ROUTER_TOOLS
    from jarvis.society.capabilities import is_never_granted

    for name in ("app-ui-snapshot", "app-ui-click", "app-ui-type", "app-ui-scroll"):
        assert name in ROUTER_TOOLS
        assert is_never_granted(name)
        assert is_never_granted(name.replace("-", "_"))


def _router_tools() -> dict[str, Any]:
    from jarvis.brain.factory import _load_tools_for_tier
    from jarvis.core.bus import EventBus
    from jarvis.core.config import JarvisConfig

    return _load_tools_for_tier(
        "router",
        bus=EventBus(),
        executor=None,
        harness_manager=None,
        user_profile=None,
        people=None,
        config=JarvisConfig(),
    )


def test_factory_wires_the_tools_with_the_bus() -> None:
    tools = _router_tools()
    for name in ("app_ui_snapshot", "app_ui_click", "app_ui_type", "app_ui_scroll"):
        assert name in tools, name
        assert tools[name].risk_tier == "safe"


class _Manager:
    def __init__(self, tools: dict[str, Any]) -> None:
        self._tools = tools


async def test_the_jarvis_mcp_catalog_offers_them(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
    from jarvis.mcp import jarvis_tools_server as server

    loaded = _router_tools()
    wanted = ("navigate", "app_ui_snapshot", "app_ui_click", "app_ui_type", "app_ui_scroll")
    gateway = BrainSupervisorToolGateway(_Manager({name: loaded[name] for name in wanted}))
    monkeypatch.setattr(server, "_gateway", lambda: gateway)

    built = server.build_server()
    listed = await built.request_handlers[types.ListToolsRequest](
        types.ListToolsRequest(method="tools/list")
    )
    by_name = {tool.name: tool for tool in listed.root.tools}

    for name in wanted:
        assert name in by_name, name
    assert by_name["app_ui_click"].inputSchema["properties"].keys() >= {"ref", "label"}
    assert by_name["navigate"].inputSchema["properties"].keys() >= {"section", "then"}
    assert by_name["app_ui_snapshot"].annotations.readOnlyHint is True


def test_request_event_carries_every_field() -> None:
    event = AppUiRequest(request_id="r", action="type", label="x", text="y", submit=True)
    assert (event.request_id, event.action, event.text, event.submit) == ("r", "type", "y", True)
