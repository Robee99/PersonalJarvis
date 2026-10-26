"""A mission typed into Jarvis's own window starts on the app, not the HUD.

Live 2026-10-26: "chrome is already opened click on roberto..." was typed in
Jarvis's chat. The mission photographed Jarvis's dark window, said "the
screenshot is too dark" and failed on step one.
"""
from __future__ import annotations

from jarvis.cu import engine
from jarvis.platform import window_state as ws

OWN = 4242


def _win(title: str, pid: int, handle: int, minimized: bool = False) -> ws.WindowInfo:
    return ws.WindowInfo(title, minimized=minimized, handle=handle, pid=pid)


JARVIS = _win("Personal Jarvis", OWN, 1)
CHROME = _win("Google Chrome", 900, 2)
NOTEPAD = _win("notes.txt - Notepad", 901, 3)


def _world(monkeypatch, *, front: ws.WindowInfo, windows, behind=None) -> list:
    raised: list = []
    monkeypatch.setattr(ws, "foreground_window", lambda: front)
    monkeypatch.setattr(ws, "list_windows", lambda: list(windows))
    monkeypatch.setattr(ws, "window_behind_jarvis", lambda: behind)
    monkeypatch.setattr(ws, "raise_window", lambda w: (raised.append(w.title) or True, w.title))
    return raised


def test_the_named_app_comes_forward(monkeypatch):
    raised = _world(monkeypatch, front=JARVIS, windows=[JARVIS, NOTEPAD, CHROME], behind=NOTEPAD)
    goal = "chrome is already opened click on roberto then go to youtube"
    assert engine._step_off_jarvis(goal, platform="win32", own_pid=OWN) == "Google Chrome"
    assert raised == ["Google Chrome"]


def test_without_a_named_app_the_window_under_jarvis_comes_forward(monkeypatch):
    raised = _world(monkeypatch, front=JARVIS, windows=[JARVIS, NOTEPAD, CHROME], behind=NOTEPAD)
    stepped = engine._step_off_jarvis("click the blue button", platform="win32", own_pid=OWN)
    assert stepped == NOTEPAD.title
    assert raised == [NOTEPAD.title]


def test_nothing_moves_when_another_app_is_already_in_front(monkeypatch):
    raised = _world(monkeypatch, front=NOTEPAD, windows=[JARVIS, NOTEPAD, CHROME], behind=None)
    assert engine._step_off_jarvis("open chrome and search", platform="win32", own_pid=OWN) == ""
    assert raised == []


def test_a_minimized_named_app_is_not_chosen(monkeypatch):
    hidden = _win("Google Chrome", 900, 2, minimized=True)
    raised = _world(monkeypatch, front=JARVIS, windows=[JARVIS, hidden], behind=None)
    assert engine._step_off_jarvis("click roberto in chrome", platform="win32", own_pid=OWN) == ""
    assert raised == []


def test_other_platforms_are_left_alone(monkeypatch):
    raised = _world(monkeypatch, front=JARVIS, windows=[JARVIS, CHROME], behind=CHROME)
    assert engine._step_off_jarvis("click roberto in chrome", platform="darwin", own_pid=OWN) == ""
    assert raised == []
