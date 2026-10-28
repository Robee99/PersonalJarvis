"""PointAtTool: aims at the element click_element would press, draws, never clicks."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.protocols import UIANode
from jarvis.plugins.tool.point_at import PointAtTool, monitor_and_fraction

_MONITORS = [(0, 0, 1920, 1080), (1920, 0, 2560, 1440)]


class _Tree:
    def __init__(self, nodes: list[UIANode], error: Exception | None = None) -> None:
        self.nodes = nodes
        self.error = error

    async def observe(self, **_: Any) -> SimpleNamespace:
        if self.error is not None:
            raise self.error
        return SimpleNamespace(nodes=tuple(self.nodes))


class _Overlay:
    def __init__(self, shown: bool = True, reason: str = "") -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = (shown, reason)

    async def point(self, *, monitor: list[int], rect: list[float], label: str):
        self.calls.append({"monitor": monitor, "rect": rect, "label": label})
        return self.result


def _tool(nodes: list[UIANode], overlay: _Overlay | None = None, **kw: Any) -> PointAtTool:
    return PointAtTool(
        vision_source=kw.get("tree") or _Tree(nodes),
        monitors=kw.get("monitors") or (lambda: _MONITORS),
        controller=overlay if overlay is not None else _Overlay(),
    )


async def test_arrow_lands_on_the_element_as_a_fraction_of_its_monitor() -> None:
    overlay = _Overlay()
    nodes = [UIANode(role="Button", name="Save", bounds=(2000, 100, 128, 40))]

    result = await _tool(nodes, overlay).execute({"name": "save"}, SimpleNamespace())

    assert result.success
    assert "Nothing was clicked" in result.output
    [call] = overlay.calls
    assert call["monitor"] == [1920, 0, 2560, 1440]
    assert call["rect"] == pytest.approx([80 / 2560, 100 / 1440, 128 / 2560, 40 / 1440])
    assert call["label"] == "Save"


async def test_exact_label_beats_an_earlier_substring_match() -> None:
    overlay = _Overlay()
    nodes = [
        UIANode(role="MenuItem", name="Save as...", bounds=(10, 10, 80, 20)),
        UIANode(role="Button", name="Save", bounds=(500, 500, 60, 20)),
    ]

    result = await _tool(nodes, overlay).execute({"name": "Save"}, SimpleNamespace())

    assert overlay.calls[0]["label"] == "Save"
    assert "2 matches" in result.output


async def test_same_matching_rules_as_click_element() -> None:
    overlay = _Overlay()
    nodes = [
        UIANode(role="Button", name="Send", bounds=(10, 10, 80, 20), enabled=False),
        UIANode(role="Button", name="Send", bounds=(10, 40, 0, 0)),
        UIANode(role="Edit", name="Send", bounds=(10, 70, 80, 20)),
        UIANode(role="Button", name="Send", bounds=(10, 100, 80, 20)),
    ]

    await _tool(nodes, overlay).execute({"name": "Send", "role": "button"}, SimpleNamespace())

    assert overlay.calls[0]["rect"][1] == pytest.approx(100 / 1080)


async def test_no_match_lists_visible_labels_and_draws_nothing() -> None:
    overlay = _Overlay()
    nodes = [UIANode(role="Button", name="Open", bounds=(10, 10, 80, 20))]

    result = await _tool(nodes, overlay).execute({"name": "Print"}, SimpleNamespace())

    assert not result.success
    assert "'Open'" in result.error
    assert overlay.calls == []


async def test_overlay_unavailable_reason_is_reported() -> None:
    overlay = _Overlay(shown=False, reason="Wayland session (no always-on-top overlay surface)")
    nodes = [UIANode(role="Button", name="Save", bounds=(10, 10, 80, 20))]

    result = await _tool(nodes, overlay).execute({"name": "Save"}, SimpleNamespace())

    assert not result.success
    assert result.error == (
        "Could not draw the arrow: Wayland session (no always-on-top overlay surface)"
    )


async def test_without_a_wired_overlay_nothing_is_observed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.cu.indicator import controller as controller_mod

    monkeypatch.setattr(controller_mod, "_controller", None)
    tree = _Tree([], error=AssertionError("must not observe without an overlay"))
    tool = PointAtTool(vision_source=tree, monitors=lambda: _MONITORS)

    result = await tool.execute({"name": "Save"}, SimpleNamespace())

    assert result.error == "The on-screen overlay is not running in this session."


async def test_tree_failure_is_reported() -> None:
    tree = _Tree([], error=RuntimeError("UIA timeout"))

    result = await _tool([], tree=tree).execute({"name": "Save"}, SimpleNamespace())

    assert result.error == "Reading the UI failed: UIA timeout"


async def test_missing_name_is_refused() -> None:
    result = await _tool([]).execute({"name": "  "}, SimpleNamespace())

    assert result.error == "Provide the element's 'name'"


def test_element_off_every_monitor_has_no_placement() -> None:
    assert monitor_and_fraction((5000, 5000, 10, 10), _MONITORS) is None
    assert monitor_and_fraction((-50, 0, 10, 10), [(-1920, 0, 1920, 1080)]) == (
        (-1920, 0, 1920, 1080),
        [1870 / 1920, 0.0, 10 / 1920, 10 / 1080],
    )


def test_contract() -> None:
    tool = PointAtTool()
    assert tool.name == "point_at"
    assert tool.risk_tier == "safe"
    assert tool.schema["required"] == ["name"]
