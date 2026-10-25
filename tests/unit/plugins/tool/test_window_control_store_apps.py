"""Closing a Store app finds its frame window (live 2026-10-25: Calculator stayed open)."""

from __future__ import annotations

import pytest

import jarvis.platform.window_state as window_state
from jarvis.platform.window_state import WindowInfo
from jarvis.plugins.tool import window_control

_WINDOWS = [
    WindowInfo(title="Calculator", handle=11, pid=100),  # ApplicationFrameHost frame
    WindowInfo(title="Calculator", handle=12, pid=200),  # CalculatorApp's own window
    WindowInfo(title="chat - Google Chrome", handle=13, pid=300),
]
_PROCESSES = {100: "applicationframehost", 200: "calculatorapp", 300: "chrome"}


@pytest.fixture(autouse=True)
def _desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(window_state, "list_windows", lambda: list(_WINDOWS))
    monkeypatch.setattr(window_control, "_process_name", lambda pid: _PROCESSES.get(pid, ""))


def test_close_by_program_tries_the_window_titled_exactly_first() -> None:
    found = window_control._candidates("calculator", program_only=True)

    assert [w.handle for w in found] == [11, 12]


def test_a_title_that_only_contains_the_word_is_still_not_closed() -> None:
    assert window_control._candidates("chat", program_only=True) == []
