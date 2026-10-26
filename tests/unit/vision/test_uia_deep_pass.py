"""The UIA deep pass: controls nested below the depth ladder.

Chrome's profile picker is a web page, and its "Open Roberto profile" button
sits about 15 levels under the window. The depth-6 traversal saw only the
window title, so click_element answered "Available labels: ['Google
Chrome']" (2026-10-26). These pin the deep pass that reaches it.
"""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

from jarvis.vision.pruning import RawNode, prune_tree
from jarvis.vision.uia_tree import _deep_query, merge_deep_controls, wants_deep_pass

_SCREEN = (0, 0, 1920, 1080)


def _window(title: str = "Google Chrome") -> RawNode:
    return RawNode(role="Window", name=title, bounds=(316, 21, 1287, 969))


def _button(name: str, x: int = 400) -> RawNode:
    return RawNode(role="Button", name=name, bounds=(x, 300, 120, 40))


def test_a_chromium_window_always_gets_a_deep_pass():
    rich = [_window()] + [_button(f"b{i}", x=10 * i) for i in range(20)]
    assert wants_deep_pass("Chrome_WidgetWin_1", rich)


def test_a_window_with_almost_no_named_controls_gets_a_deep_pass():
    assert wants_deep_pass("HwndWrapper[App.exe]", [_window("App"), _button("OK")])


def test_a_native_window_with_its_controls_in_reach_is_left_alone():
    rich = [_window("Notepad")] + [_button(f"b{i}", x=10 * i) for i in range(8)]
    assert not wants_deep_pass("Notepad", rich)


def test_the_profile_button_survives_pruning_after_the_merge():
    deep = [_button("Open Roberto profile"), _button("More actions for Roberto", x=560)]
    merged = merge_deep_controls([_window()], deep)
    kept = prune_tree(merged, max_depth=4, monitor_bounds=_SCREEN)
    assert [n.name for n in kept] == [
        "Google Chrome",
        "Open Roberto profile",
        "More actions for Roberto",
    ]
    assert all(n.parent_index == 0 for n in kept[1:])


def test_a_control_the_shallow_pass_already_has_is_not_added_twice():
    shallow = [_window(), _button("Reload")]
    merged = merge_deep_controls(shallow, [_button("Reload"), _button("Search")])
    assert [n.name for n in merged] == ["Google Chrome", "Reload", "Search"]


def test_the_merge_never_pushes_the_tree_into_the_overflow_fallback():
    deep = [_button(f"link {i}", x=i) for i in range(400)]
    kept = prune_tree(merge_deep_controls([_window()], deep), max_depth=6, monitor_bounds=_SCREEN)
    assert 100 < len(kept) <= 150


# --- the cached query itself, against a fake UIA client ----------------------


class _Rect:
    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


def _cached(name: str, *, ctype: int = 50000, rect: _Rect | None = None, **state: Any) -> Any:
    return SimpleNamespace(
        CachedName=name,
        CachedAutomationId=state.get("automation_id", ""),
        CachedControlType=ctype,
        CachedBoundingRectangle=rect or _Rect(400, 300, 520, 340),
        CachedIsEnabled=True,
        CachedIsOffscreen=state.get("offscreen", False),
        CachedIsPassword=state.get("password", False),
        CachedHasKeyboardFocus=False,
    )


class _Found:
    def __init__(self, items: list[Any]) -> None:
        self._items = items
        self.Length = len(items)

    def GetElement(self, i: int) -> Any:  # noqa: N802 - COM spelling
        return self._items[i]


class _CacheRequest:
    def __init__(self) -> None:
        self.props: list[int] = []

    def AddProperty(self, prop: int) -> None:  # noqa: N802 - COM spelling
        self.props.append(prop)


class _Client:
    def CreatePropertyCondition(self, prop: int, value: int) -> tuple[int, int]:  # noqa: N802
        return (prop, value)

    def CreateOrConditionFromArray(self, conditions: list[Any]) -> tuple[str, list[Any]]:  # noqa: N802
        return ("or", conditions)

    def CreateCacheRequest(self) -> _CacheRequest:  # noqa: N802
        return _CacheRequest()


def _install_fake_uia(monkeypatch) -> None:
    dll = SimpleNamespace(
        TreeScope_Descendants=4,
        UIA_ControlTypePropertyId=30003,
        UIA_NamePropertyId=30005,
        UIA_BoundingRectanglePropertyId=30001,
        UIA_IsEnabledPropertyId=30010,
        UIA_IsOffscreenPropertyId=30022,
        UIA_AutomationIdPropertyId=30011,
        UIA_IsPasswordPropertyId=30019,
        UIA_HasKeyboardFocusPropertyId=30008,
    )
    uia = SimpleNamespace(
        UIA_dll=dll,
        iuia=_Client(),
        known_control_types={"Button": 50000, "Edit": 50004, "Hyperlink": 50005},
        known_control_type_ids={50000: "Button", 50004: "Edit", 50005: "Hyperlink"},
    )
    defines = ModuleType("pywinauto.uia_defines")
    defines.IUIA = lambda: uia  # type: ignore[attr-defined]
    package = ModuleType("pywinauto")
    monkeypatch.setitem(sys.modules, "pywinauto", package)
    monkeypatch.setitem(sys.modules, "pywinauto.uia_defines", defines)


def _root(items: list[Any], calls: list[Any]) -> Any:
    def find_all(scope: int, condition: Any, cache: Any) -> _Found:
        calls.append((scope, condition))
        return _Found(items)

    return SimpleNamespace(element=SimpleNamespace(FindAllBuildCache=find_all))


def test_the_deep_query_keeps_named_on_screen_controls(monkeypatch):
    _install_fake_uia(monkeypatch)
    calls: list[Any] = []
    items = [
        _cached("Open Roberto profile"),
        _cached("Search", ctype=50004, password=False),
        _cached("", automation_id=""),
        _cached("Hidden", offscreen=True),
        _cached("Flat", rect=_Rect(10, 10, 10, 30)),
        _cached("Password", ctype=50004, password=True),
    ]
    out = _deep_query(_root(items, calls))
    assert [(n.role, n.name) for n in out] == [
        ("Button", "Open Roberto profile"),
        ("Edit", "Search"),
        ("Edit", "Password"),
    ]
    assert out[0].bounds == (400, 300, 120, 40)
    assert out[2].is_password and out[2].value == ""
    scope, (kind, conditions) = calls[0]
    assert scope == 4 and kind == "or"
    assert {value for _prop, value in conditions} == {50000, 50004, 50005}


def test_the_deep_query_gives_up_quietly_without_uia(monkeypatch):
    monkeypatch.setitem(sys.modules, "pywinauto.uia_defines", None)
    assert _deep_query(SimpleNamespace()) == []
