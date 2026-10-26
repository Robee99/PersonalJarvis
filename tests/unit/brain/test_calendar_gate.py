"""Calendar asks while no calendar is connected (jarvis/brain/calendar_gate.py).

Battery 2026-10-26: "What is on my calendar today?" ran a web search.
"""

from __future__ import annotations

import pytest

from jarvis.brain.calendar_gate import is_calendar_ask, not_connected_reply


@pytest.mark.parametrize(
    "ask",
    [
        "What is on my calendar today?",
        "check my calendar",
        "Do I have any meetings tomorrow?",
        "what's my schedule for today",
        "any appointments this week",
        "aaj ki meetings batao",
        "calendar mein kya hai",
    ],
)
def test_calendar_asks_are_recognised(ask: str) -> None:
    assert is_calendar_ask(ask)


@pytest.mark.parametrize(
    "ask",
    [
        "how do I share a google calendar",
        "what is a calendar year",
        "set a timer for 2 minutes",
        "open the calendar app",
        "open calendar",
        "schedule a tweet",
    ],
)
def test_other_asks_are_left_alone(ask: str) -> None:
    assert not is_calendar_ask(ask)


def test_the_reply_names_the_one_step() -> None:
    reply = not_connected_reply("Sir")
    assert reply.startswith("Your calendar isn't connected yet, Sir.")
    assert "Plugins" in reply and "Google Calendar" in reply
    assert not_connected_reply("").startswith("Your calendar isn't connected yet.")
