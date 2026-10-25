"""app_control (desktop_driver.py) drives a desktop app through CUA Driver in one call.

Owner's test 2026-10-25: CUA Driver pressed Calculator's buttons and typed
into Notepad in the background, but Gemma could not chain its window ids and
element tokens. The fake driver below answers in the shapes cua-driver 0.28.2
printed that day.
"""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.brain import desktop_driver
from jarvis.brain.desktop_driver import AppControlTool, CuaError, find_button, read_out

_CALC_ELEMENTS = [
    {"element_index": 0, "element_token": "s1:0", "label": "Calculator", "role": "Window"},
    {"element_index": 8, "element_token": "s1:8", "label": "Display is 0", "role": "Text"},
    {"element_index": 22, "element_token": "s1:22", "label": "Plus", "role": "Button"},
    {"element_index": 23, "element_token": "s1:23", "label": "Equals", "role": "Button"},
    {"element_index": 24, "element_token": "s1:24", "label": "Multiply by", "role": "Button"},
    {"element_index": 31, "element_token": "s1:31", "label": "Seven", "role": "Button"},
    {"element_index": 32, "element_token": "s1:32", "label": "Eight", "role": "Button"},
]


_EQUALS = _CALC_ELEMENTS[3]["element_token"]


class _FakeDriver:
    """Records every call; Calculator's display shows 15 after Equals."""

    def __init__(self, *, running: bool = True, scramble: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.running = running
        self.scramble = scramble
        self.display = "Display is 0"
        self.text = ""

    def windows(self) -> list[dict[str, Any]]:
        out = [
            {
                "app_name": "chrome.exe",
                "pid": 9,
                "title": "Calculator - Google Search",
                "window_id": 90,
            }
        ]
        if self.running:
            out.append(
                {
                    "app_name": "ApplicationFrameHost.exe",
                    "pid": 18540,
                    "title": "Calculator",
                    "window_id": 591692,
                }
            )
            out.append(
                {
                    "app_name": "Notepad.exe",
                    "pid": 2668,
                    "title": "Untitled - Notepad",
                    "window_id": 700,
                }
            )
        return out

    async def __call__(self, exe: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, args))
        if tool == "list_windows":
            return {"windows": self.windows()}
        if tool == "launch_app":
            self.running = True
            # As cua-driver 0.28.2 answers: the pid beside windows that lack it.
            return {
                "pid": 18540,
                "windows": [{"title": "Calculator", "window_id": 591692, "is_on_screen": True}],
            }
        if tool == "get_window_state":
            if args["pid"] == 2668:
                return {
                    "elements": [
                        {
                            "element_token": "n:1",
                            "role": "Document",
                            "value": self.text,
                            "actions": ["set_value"],
                        }
                    ]
                }
            return {
                "elements": [
                    {**e, "label": self.display} if e["role"] == "Text" else e
                    for e in _CALC_ELEMENTS
                ]
            }
        if tool == "click":
            if args["element_token"] == _EQUALS:
                self.display = "Display is 15"
            return {"ok": True}
        if tool == "type_text":
            text = args["text"]
            self.text += text.replace("run", "urn") if self.scramble else text
            return {"ok": True}
        if tool == "set_value":
            self.text = args["value"]
            return {"ok": True}
        raise CuaError(f"unexpected {tool}")


@pytest.fixture
def driver(monkeypatch: pytest.MonkeyPatch) -> _FakeDriver:
    fake = _FakeDriver()
    monkeypatch.setattr(desktop_driver, "run_driver", fake)
    return fake


def test_spoken_buttons_find_their_accessible_names() -> None:
    assert find_button(_CALC_ELEMENTS, "7")["label"] == "Seven"
    assert find_button(_CALC_ELEMENTS, "+")["label"] == "Plus"
    assert find_button(_CALC_ELEMENTS, "x")["label"] == "Multiply by"
    assert find_button(_CALC_ELEMENTS, "equals")["label"] == "Equals"
    assert find_button(_CALC_ELEMENTS, "9") is None


def test_the_window_reads_out_its_display() -> None:
    assert read_out(_CALC_ELEMENTS) == "Shows: Display is 0"


async def test_calculator_presses_in_order_and_reads_the_result(driver: _FakeDriver) -> None:
    result = await AppControlTool(exe="cua").execute(
        {"app": "Calculator", "press": ["7", "+", "8", "="]}, None
    )

    assert result.success, result.error
    assert result.output == "Calculator: Pressed Seven, Plus, Eight, Equals. Shows: Display is 15"
    clicks = [a["element_token"] for t, a in driver.calls if t == "click"]
    assert clicks == ["s1:31", "s1:22", "s1:32", "s1:23"]
    # The Calculator app, never the browser tab with the same word in its title.
    assert all(a.get("pid") == 18540 for t, a in driver.calls if t == "click")
    assert not any(t == "launch_app" for t, _ in driver.calls)


async def test_an_app_that_is_not_running_is_opened_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeDriver(running=False)
    monkeypatch.setattr(desktop_driver, "run_driver", fake)

    result = await AppControlTool(exe="cua").execute({"app": "calculator", "press": ["7"]}, None)

    assert result.success, result.error
    assert [t for t, _ in fake.calls][:2] == ["list_windows", "launch_app"]


async def test_a_missing_button_lists_what_the_window_has(driver: _FakeDriver) -> None:
    result = await AppControlTool(exe="cua").execute(
        {"app": "Calculator", "press": ["7", "cube root"]}, None
    )

    assert not result.success
    assert "no button 'cube root' after pressing Seven" in (result.error or "")
    assert "Multiply by" in (result.error or "")


async def test_typed_text_is_read_back(driver: _FakeDriver) -> None:
    result = await AppControlTool(exe="cua").execute(
        {"app": "Notepad", "type": "hello from jarvis"}, None
    )

    assert result.success, result.error
    assert result.output.endswith("Text: 'hello from jarvis'")
    assert not any(t == "set_value" for t, _ in driver.calls)


async def test_jumbled_typing_into_an_empty_field_is_set_right(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeDriver(scramble=True)
    monkeypatch.setattr(desktop_driver, "run_driver", fake)

    result = await AppControlTool(exe="cua").execute(
        {"app": "Notepad", "type": "hello from cua run 1"}, None
    )

    assert result.output.endswith("Text: 'hello from cua run 1'")
    assert (
        "set_value",
        {"pid": 2668, "window_id": 700, "element_token": "n:1", "value": "hello from cua run 1"},
    ) in fake.calls


async def test_jumbled_typing_after_existing_text_is_left_for_the_model_to_see(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeDriver(scramble=True)
    fake.text = "notes "
    monkeypatch.setattr(desktop_driver, "run_driver", fake)

    result = await AppControlTool(exe="cua").execute({"app": "Notepad", "type": "run"}, None)

    # Replacing the field would erase what was there; the read-back shows the slip.
    assert result.output.endswith("Text: 'notes urn'")
    assert not any(t == "set_value" for t, _ in fake.calls)


async def test_a_driver_error_is_reported_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(exe: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        raise CuaError("No window with window_id 5678 exists.")

    monkeypatch.setattr(desktop_driver, "run_driver", broken)

    result = await AppControlTool(exe="cua").execute({"app": "Calculator"}, None)

    assert not result.success
    assert result.error == "Calculator: No window with window_id 5678 exists."


async def test_without_the_driver_it_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop_driver.sys, "platform", "linux")

    result = await AppControlTool().execute({"app": "Calculator"}, None)

    assert not result.success
    assert "CUA Driver is not installed" in (result.error or "")
    assert desktop_driver.app_control_tool() is None
