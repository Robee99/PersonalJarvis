"""Timers and reminders Jarvis keeps itself (jarvis/focus/timers.py).

Battery 2026-10-26: "Set a timer for 2 minutes" and "Remind me in 10 minutes
to drink water" ended on words only, with nothing set. Contract:

1. Plain timer and reminder asks are set without a model and say when they ring.
2. A due entry is said with the honorific and shown as a notification.
3. Pending entries survive a restart; one that fell due while Jarvis was
   closed rings at once and says when it was due.
4. Cancel and status answer from the board; anything else is ``None``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from jarvis.focus.timers import (
    TimerBoard,
    parse_clock,
    parse_duration,
    spoken_duration,
    timer_reflex,
)


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _Ears:
    """Collects what the board says and shows."""

    def __init__(self) -> None:
        self.said: list[str] = []
        self.toasts: list[tuple[str, str]] = []

    async def say(self, text: str) -> None:
        self.said.append(text)

    def toast(self, title: str, body: str) -> None:
        self.toasts.append((title, body))


def _board(tmp_path: Path, ears: _Ears, now: float = 1_000_000.0) -> TimerBoard:
    return TimerBoard(tmp_path / "timers.json", clock=_Clock(now), toast=ears.toast)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("2 minutes", 120),
        ("ten minutes", 600),
        ("an hour and 30 minutes", 5400),
        ("half an hour", 1800),
        ("ninety seconds", 90),
        ("1.5 hours", 5400),
        ("twenty five minutes", 1500),
    ],
)
def test_durations_are_read(text: str, seconds: int) -> None:
    assert parse_duration(text) == seconds


def test_durations_beyond_a_week_or_unreadable_are_refused() -> None:
    assert parse_duration("900 hours") is None
    assert parse_duration("several minutes") is None
    assert parse_duration("soon") is None


def test_clock_times_are_the_next_occurrence() -> None:
    now = datetime(2026, 10, 26, 14, 0)
    assert parse_clock("5:30 pm", now) == datetime(2026, 10, 26, 17, 30)
    assert parse_clock("17:30", now) == datetime(2026, 10, 26, 17, 30)
    assert parse_clock("9 am", now) == datetime(2026, 10, 27, 9, 0)
    assert parse_clock("13 pm", now) is None


def test_spoken_duration() -> None:
    assert spoken_duration(120) == "2 minutes"
    assert spoken_duration(3661) == "1 hour and 1 minute and 1 second"


@pytest.mark.parametrize(
    "ask",
    [
        "Set a timer for 2 minutes",
        "set a 2 minute timer please",
        "Jarvis, start a timer for two minutes.",
        "timer for 2 minutes",
        "a 2 minute timer",
    ],
)
async def test_timer_asks_set_a_timer(tmp_path: Path, ask: str) -> None:
    ears = _Ears()
    board = _board(tmp_path, ears)
    reply = await timer_reflex(ask, board=board)
    assert reply is not None and reply.startswith("Timer set for 2 minutes. It rings at ")
    (entry,) = board.pending()
    assert entry.kind == "timer" and entry.seconds == 120
    board.cancel("timer")


@pytest.mark.parametrize(
    ("ask", "what"),
    [
        ("Remind me in 10 minutes to drink water", "drink water"),
        ("remind me to drink water in 10 minutes", "drink water"),
        ("Set a reminder in 10 minutes to stretch", "stretch"),
    ],
)
async def test_reminder_asks_set_a_reminder(tmp_path: Path, ask: str, what: str) -> None:
    ears = _Ears()
    board = _board(tmp_path, ears)
    reply = await timer_reflex(ask, board=board)
    assert reply is not None and reply.startswith(f"I'll remind you to {what} at ")
    (entry,) = board.pending("reminder")
    assert entry.label == what and entry.due == pytest.approx(1_000_600.0)
    board.cancel("reminder")


async def test_a_clock_reminder_is_set_for_that_time(tmp_path: Path) -> None:
    ears = _Ears()
    now = datetime(2026, 10, 26, 14, 0)
    board = _board(tmp_path, ears, now=now.timestamp())
    reply = await timer_reflex("remind me to call mum at 5:30 pm", board=board, now=now)
    assert reply == "I'll remind you to call mum at 5:30 PM."
    (entry,) = board.pending()
    assert entry.due == pytest.approx(datetime(2026, 10, 26, 17, 30).timestamp())
    board.cancel("reminder")


@pytest.mark.parametrize(
    "ask",
    [
        "how do timers work on windows",
        "don't set a timer for 2 minutes",
        "what is a reminder",
        "set the brightness to 40",
        "remind me later",
    ],
)
async def test_other_asks_reach_the_brain(tmp_path: Path, ask: str) -> None:
    board = _board(tmp_path, _Ears())
    assert await timer_reflex(ask, board=board) is None
    assert board.pending() == []


async def test_a_due_timer_is_said_and_shown(tmp_path: Path) -> None:
    ears = _Ears()
    board = _board(tmp_path, ears)
    board.bind(ears.say, "Sir")
    board.add("timer", 0.01)
    await asyncio.sleep(0.05)
    assert ears.said == ["Sir, your 0 seconds timer is done."]
    assert ears.toasts and ears.toasts[0][0] == "Jarvis: Timer done"
    assert board.pending() == []


async def test_a_due_reminder_uses_the_honorific(tmp_path: Path) -> None:
    ears = _Ears()
    board = _board(tmp_path, ears)
    board.bind(ears.say, "Sir")
    entry = board.add("reminder", 0.01, "drink water")
    await asyncio.sleep(0.05)
    assert ears.said == ["Sir, a reminder: drink water."]
    assert board.ring_text(entry) == "Sir, a reminder: drink water."


def test_no_honorific_starts_with_a_capital(tmp_path: Path) -> None:
    board = _board(tmp_path, _Ears())
    board.bind(None, "")
    entry = board.add("timer", 120)
    assert board.ring_text(entry) == "Your 2 minutes timer is done."


async def test_pending_entries_survive_a_restart(tmp_path: Path) -> None:
    ears = _Ears()
    first = _board(tmp_path, ears)
    first.bind(ears.say, "Sir")
    first.add("reminder", 600, "drink water")
    for task in list(first._tasks.values()):
        task.cancel()

    later = TimerBoard(tmp_path / "timers.json", clock=_Clock(1_000_900.0), toast=ears.toast)
    later.bind(ears.say, "Sir")
    await asyncio.sleep(0.02)
    assert len(ears.said) == 1
    assert ears.said[0].startswith("Sir, a reminder: drink water. It was due at ")
    assert ears.said[0].endswith(", while I was closed.")
    assert later.pending() == []
    assert (tmp_path / "timers.json").read_text(encoding="utf-8").strip() == "[]"


async def test_cancel_and_status(tmp_path: Path) -> None:
    board = _board(tmp_path, _Ears())
    assert await timer_reflex("cancel the timer", board=board) == "There was no timer set."
    assert (
        await timer_reflex("what reminders do I have", board=board)
        == "There are no timers or reminders set."
    )
    await timer_reflex("set a timer for 5 minutes", board=board)
    status = await timer_reflex("how long is left on the timer", board=board)
    assert status == "You have a 5 minutes timer with 5 minutes left."
    assert await timer_reflex("cancel the timer", board=board) == "Cancelled 1 timer."
    assert board.pending() == []


def test_an_unreadable_file_starts_empty(tmp_path: Path) -> None:
    (tmp_path / "timers.json").write_text("not json", encoding="utf-8")
    board = _board(tmp_path, _Ears())
    assert board.pending() == []


async def test_a_reminder_with_no_subject_still_rings(tmp_path: Path) -> None:
    ears = _Ears()
    board = _board(tmp_path, ears)
    board.bind(None, "Sir")
    reply = await timer_reflex("Remind me in 10 minutes.", board=board)
    assert reply is not None and reply.startswith("I'll remind you at ")
    (entry,) = board.pending()
    assert board.ring_text(entry) == "Sir, this is the reminder you asked for."
    board.cancel("reminder")
