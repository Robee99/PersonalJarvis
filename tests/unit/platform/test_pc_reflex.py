"""Volume and window reflexes: plain commands only, replies from the read-back."""

from __future__ import annotations

import pytest

from jarvis.platform import pc_reflex
from jarvis.platform.pc_reflex import (
    VolumeCommand,
    WindowCommand,
    parse_volume_command,
    parse_window_command,
)
from jarvis.plugins.tool.window_control import WindowResult


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("set the volume to 30 percent", VolumeCommand("set", 30)),
        ("volume 40", VolumeCommand("set", 40)),
        ("Jarvis, volume to 75%", VolumeCommand("set", 75)),
        ("set volume to 250", VolumeCommand("set", 100)),
        ("turn the volume up", VolumeCommand("up", 10)),
        ("turn down the volume", VolumeCommand("down", 10)),
        ("volume down", VolumeCommand("down", 10)),
        ("lower the volume by 20", VolumeCommand("down", 20)),
        ("increase volume", VolumeCommand("up", 10)),
        ("louder", VolumeCommand("up", 10)),
        ("make it quieter please", VolumeCommand("down", 10)),
        ("mute", VolumeCommand("mute")),
        ("mute the sound", VolumeCommand("mute")),
        ("unmute.", VolumeCommand("unmute")),
    ],
)
def test_plain_volume_commands_match(text: str, expected: VolumeCommand) -> None:
    assert parse_volume_command(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "unmute and set volume to 50",
        "don't change the volume",
        "what is the volume?",
        "how do I turn the volume up",
        "set the spotify volume to 30",
        "mute discord",
        "set the volume to 20 when the movie starts",
        "set the brightness to 40",
        "turn the volume up and open spotify",
    ],
)
def test_volume_leaves_everything_else_to_the_brain(text: str) -> None:
    assert parse_volume_command(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("maximize notepad", WindowCommand("maximize", "notepad")),
        ("Minimise the Chrome window", WindowCommand("minimize", "chrome")),
        ("restore spotify", WindowCommand("restore", "spotify")),
        ("close notepad please", WindowCommand("close", "notepad")),
        ("bring notepad back", WindowCommand("focus", "notepad")),
        ("bring back chrome", WindowCommand("focus", "chrome")),
        ("switch to visual studio code", WindowCommand("focus", "visual studio code")),
        ("Jarvis, focus discord.", WindowCommand("focus", "discord")),
    ],
)
def test_plain_window_commands_match(text: str, expected: WindowCommand) -> None:
    assert parse_window_command(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "close it",
        "close the tab",
        "close all windows",
        "don't close notepad",
        "should I close notepad?",
        "close notepad and open chrome",
        "maximize the window",
        "close the door to the garden behind the shed",
        "open notepad",
    ],
)
def test_window_leaves_everything_else_to_the_brain(text: str) -> None:
    assert parse_window_command(text) is None


def test_window_reply_states_the_read_back(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.plugins.tool.window_control as wc

    monkeypatch.setattr(pc_reflex.sys, "platform", "win32")
    normal = WindowResult("Untitled - Notepad", "normal")
    monkeypatch.setattr(wc, "change", lambda action, target, **kw: normal)
    # Windows reported the window still normal: the reply says so, not "maximized".
    assert pc_reflex.window_reflex("maximize notepad") == "Notepad is open."
    maximized = WindowResult("Untitled - Notepad", "maximized")
    monkeypatch.setattr(wc, "change", lambda action, target, **kw: maximized)
    assert pc_reflex.window_reflex("maximize notepad") == "Notepad is maximized."


def test_window_not_found_goes_to_the_brain(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.plugins.tool.window_control as wc

    def missing(action: str, target: str, **kw: object) -> WindowResult:
        raise LookupError(target)

    monkeypatch.setattr(pc_reflex.sys, "platform", "win32")
    monkeypatch.setattr(wc, "change", missing)
    assert pc_reflex.window_reflex("close downloads") is None


def test_volume_reply_is_the_read_back(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.plugins.tool.system_volume as sv

    monkeypatch.setattr(pc_reflex.sys, "platform", "win32")
    monkeypatch.setattr(sv, "apply", lambda action, percent: f"Volume is {percent}%.")
    assert pc_reflex.volume_reflex("volume 40") == "Volume is 40%."


def test_reflexes_stand_down_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pc_reflex.sys, "platform", "linux")
    assert pc_reflex.volume_reflex("mute") is None
    assert pc_reflex.window_reflex("maximize notepad") is None


def test_close_matches_the_program_only(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.plugins.tool.window_control as wc

    seen: dict[str, object] = {}

    def record(action: str, target: str, **kw: object) -> WindowResult:
        seen.update(kw, action=action)
        return WindowResult("Untitled - Notepad", "closed")

    monkeypatch.setattr(pc_reflex.sys, "platform", "win32")
    monkeypatch.setattr(wc, "change", record)
    assert pc_reflex.window_reflex("close notepad") == "Notepad is closed."
    assert seen == {"action": "close", "program_only": True}
    pc_reflex.window_reflex("maximize notepad")
    assert seen["program_only"] is False
