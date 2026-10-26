"""GEV's own tool result moves the World screen (jarvis/intel/gev_view.py).

Live 2026-10-26: "take me to Mumbai in God's Eye View" called
show_in_gods_eye_view three times, each a success, and the globe never moved.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from jarvis.core.events import NavigateSidebar, WorldIntelCommand
from jarvis.intel.gev_view import gev_view_from_result, show_gev_view
from jarvis.mcp import adapter

VIEW = {"camera": {"lat": 19.055, "lon": 72.8692, "altitude_m": 44521}, "layers": []}
TEXTS = [
    "Open Mumbai in God's Eye View: http://127.0.0.1:4173/#v=2&lat=19.0550",
    json.dumps({"url": "http://127.0.0.1:4173/#v=2", "view": VIEW}),
]


@dataclass
class _Text:
    text: str


@dataclass
class _Result:
    content: list[_Text]
    structuredContent: dict[str, Any] | None = None  # noqa: N815 - the MCP field name


def test_the_view_is_read_from_every_result_shape() -> None:
    assert gev_view_from_result(TEXTS) == VIEW
    assert gev_view_from_result(_Result([_Text(t) for t in TEXTS])) == VIEW
    assert gev_view_from_result(_Result([], {"url": "x", "view": VIEW})) == VIEW


def test_no_camera_or_a_bad_position_is_no_view() -> None:
    assert gev_view_from_result(["Opened.", json.dumps({"view": {"layers": []}})]) is None
    bad = {"view": {"camera": {"lat": 400, "lon": 0}}}
    assert gev_view_from_result(json.dumps(bad)) is None


@dataclass
class _Bus:
    events: list[Any] = field(default_factory=list)
    connected: bool = True

    async def __call__(self, events: list[Any]) -> bool:
        self.events.extend(events)
        return self.connected


async def test_the_world_screen_opens_and_flies() -> None:
    bus = _Bus()
    assert await show_gev_view(VIEW, publish=bus)
    nav, command = bus.events
    assert isinstance(nav, NavigateSidebar) and nav.section == "world-intelligence"
    assert isinstance(command, WorldIntelCommand) and command.action == "view"
    assert json.loads(command.view) == VIEW


async def test_the_tool_result_says_whether_the_screen_moved(monkeypatch) -> None:
    from jarvis.intel import gev_view

    bus = _Bus()
    monkeypatch.setattr(gev_view.show_gev_view, "__defaults__", (bus,))
    out = await adapter._show_on_world_screen(_Result([_Text(t) for t in TEXTS]), TEXTS)
    assert out[:2] == TEXTS and out[2].startswith("Shown now")

    bus.connected = False
    out = await adapter._show_on_world_screen(_Result([_Text(t) for t in TEXTS]), TEXTS)
    assert out[2].startswith("Not shown")
