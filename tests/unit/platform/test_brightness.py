"""The brightness reflex: plain commands only, and only claims what Windows reads back."""

from __future__ import annotations

import pytest

from jarvis.platform import brightness
from jarvis.platform.brightness import (
    BrightnessCommand,
    apply_brightness,
    brightness_reflex,
    parse_brightness_command,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("set the brightness to 40", BrightnessCommand(level=40)),
        ("Set screen brightness to 75%", BrightnessCommand(level=75)),
        ("brightness 30 percent please", BrightnessCommand(level=30)),
        ("Jarvis, set brightness to 250", BrightnessCommand(level=100)),
        ("turn the brightness up", BrightnessCommand(step=20)),
        ("turn down the brightness", BrightnessCommand(step=-20)),
        ("lower the brightness by 10", BrightnessCommand(step=-10)),
        ("increase brightness", BrightnessCommand(step=20)),
        ("make the screen brighter", BrightnessCommand(step=20)),
        ("dim the screen", BrightnessCommand(step=-20)),
    ],
)
def test_plain_commands_match(text: str, expected: BrightnessCommand) -> None:
    assert parse_brightness_command(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Don't change brightness.",
        "do not touch the brightness",
        "Can you tell me how to change brightness?",
        "Why is brightness stuck?",
        "how do I set the brightness to 40",
        "what is the brightness",
        "set the brightness to 40 when the movie starts",
        "leave the brightness at 50",
        "set the volume to 40",
        "open the brightness settings",
    ],
)
def test_negations_questions_and_conditions_never_match(text: str) -> None:
    assert parse_brightness_command(text) is None


class FakeWindows:
    """WMI as PowerShell answers it: a level that a write may or may not move."""

    def __init__(self, level: int | None, *, obeys: bool = True, refuses: bool = False) -> None:
        self.level = level
        self.obeys = obeys
        self.refuses = refuses
        self.writes: list[int] = []

    def __call__(self, script: str) -> tuple[int, str]:
        if "WmiSetBrightness" in script:
            if self.refuses:
                return 1, ""
            target = int(script.split("[byte]")[1].split("}")[0])
            self.writes.append(target)
            if self.obeys:
                self.level = target
            return 0, ""
        if self.level is None:
            return 1, ""
        return 0, f"{self.level}\n"


def _apply(command: BrightnessCommand, windows: FakeWindows) -> str:
    return apply_brightness(command, run=windows, sleep=lambda _s: None)


def test_a_change_is_reported_only_with_the_level_windows_reads_back() -> None:
    windows = FakeWindows(50)
    assert _apply(BrightnessCommand(level=40), windows) == "Brightness is now 40%."
    assert _apply(BrightnessCommand(step=20), windows) == "Brightness is now 60%."
    assert windows.writes == [40, 60]


def test_a_write_that_does_not_stick_is_not_claimed() -> None:
    reply = _apply(BrightnessCommand(level=40), FakeWindows(50, obeys=False))
    assert "can't confirm" in reply and "50%" in reply
    assert "now 40" not in reply


def test_a_screen_without_brightness_control_is_said_plainly() -> None:
    windows = FakeWindows(None)
    reply = _apply(BrightnessCommand(level=40), windows)
    assert "can't change the brightness of this screen" in reply
    assert windows.writes == []


def test_a_refused_write_says_the_level_is_unchanged() -> None:
    reply = _apply(BrightnessCommand(level=40), FakeWindows(50, refuses=True))
    assert reply == "Windows refused the brightness change; it is still at 50%."


def test_the_reflex_stands_down_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brightness.sys, "platform", "linux")
    assert brightness_reflex("set the brightness to 40") is None
