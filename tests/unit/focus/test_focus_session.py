"""Focus sessions: the server-side timer that names the window you drifted to.

From the owner's prompt pack (2026-10-25, prompts 9-12). Contract:

1. Started from another window, the session locks on it; started from
   Jarvis, the lock waits for the first window held for two ticks, and says so.
2. A browser locks its tab; two ticks elsewhere is one drift, called out by
   name once per cooldown. Jarvis's own window is never a drift.
3. Voice starts, moves and stops the session without a model.
4. ``diag`` reports booleans only.
"""

from __future__ import annotations

import pytest

from jarvis.focus.session import (
    CALLOUT_COOLDOWN_S,
    FocusSession,
    focus_reflex,
    parse_minutes,
    surface,
)


class _Desk:
    """A fake foreground window and clock."""

    def __init__(self, title: str) -> None:
        self.title = title
        self.now = 1000.0
        self.said: list[str] = []

    def read(self) -> str:
        return self.title

    def clock(self) -> float:
        return self.now

    async def say(self, text: str) -> None:
        self.said.append(text)


def _session(desk: _Desk) -> FocusSession:
    return FocusSession(read_title=desk.read, clock=desk.clock, say=desk.say)


async def _ticks(session: FocusSession, desk: _Desk, n: int) -> None:
    for _ in range(n):
        desk.now += 5
        await session.tick()


def test_a_title_splits_into_app_and_page() -> None:
    assert surface("Report - Google Docs - Google Chrome") == (
        "Google Chrome",
        "Report - Google Docs",
    )
    assert surface("Untitled - Notepad") == ("Notepad", "Untitled")
    assert surface("Spotify") == ("Spotify", "")


def test_minutes_are_read_from_words_and_digits() -> None:
    assert parse_minutes("thirty minutes") == 30
    assert parse_minutes("25 min") == 25
    assert parse_minutes("half an hour") == 30
    assert parse_minutes("an hour") == 60
    assert parse_minutes("banana minutes") is None
    assert parse_minutes("999 minutes") is None


@pytest.mark.asyncio
async def test_started_from_another_window_it_locks_at_once() -> None:
    desk = _Desk("budget.xlsx - Excel")
    session = _session(desk)

    line = session.start(25)

    assert "25 minutes on Excel" in line
    assert session.snapshot()["target"] == "Excel"
    assert session.snapshot()["deferred"] is False


@pytest.mark.asyncio
async def test_started_from_jarvis_the_lock_waits_and_is_announced() -> None:
    desk = _Desk("Jarvis")
    session = _session(desk)

    line = session.start(30)
    assert "I will lock on the first window you stay in" in line
    assert session.snapshot()["deferred"] is True

    desk.title = "Report - Google Docs - Google Chrome"
    await _ticks(session, desk, 2)

    assert session.snapshot()["target"] == "Report - Google Docs"
    assert session.snapshot()["lock"] == "tab"
    assert desk.said[-1].startswith("Locked on Report - Google Docs, Sir.")
    assert "what are we focusing on" in desk.said[-1]


@pytest.mark.asyncio
async def test_a_drift_is_named_once_per_cooldown_and_jarvis_is_neutral() -> None:
    desk = _Desk("Report - Google Docs - Google Chrome")
    session = _session(desk)
    session.start(30)

    desk.title = "Jarvis"
    await _ticks(session, desk, 3)
    assert session.drifts == 0

    desk.title = "Funny cats - YouTube - Google Chrome"
    await _ticks(session, desk, 4)
    assert session.drifts == 1
    assert len(desk.said) == 1
    assert "Funny cats - YouTube" in desk.said[0]
    assert "Sir" in desk.said[0]

    desk.title = "Report - Google Docs - Google Chrome"
    await _ticks(session, desk, 1)
    desk.title = "Reddit - Google Chrome"
    await _ticks(session, desk, 2)
    assert session.drifts == 2
    assert len(desk.said) == 1  # inside the cooldown: counted, not spoken

    desk.now += CALLOUT_COOLDOWN_S
    desk.title = "Report - Google Docs - Google Chrome"
    await _ticks(session, desk, 1)
    desk.title = "Spotify"
    await _ticks(session, desk, 2)
    assert len(desk.said) == 2
    assert "Spotify" in desk.said[1]


@pytest.mark.asyncio
async def test_time_up_speaks_the_report_and_ends() -> None:
    desk = _Desk("notes.txt - Notepad")
    session = _session(desk)
    session.start(1)

    desk.now += 61
    await session.tick()

    assert not session.active
    assert desk.said[-1].startswith("Time, Sir. 1 minute: on target")


@pytest.mark.asyncio
async def test_a_phone_glance_counts_as_a_drift() -> None:
    desk = _Desk("notes.txt - Notepad")
    session = _session(desk)
    session.start(30)

    line = session.note_phone()

    assert line is not None and "Phone down, Sir" in line
    assert session.phone_drifts == 1


@pytest.mark.asyncio
async def test_voice_starts_locks_and_stops_without_a_model() -> None:
    desk = _Desk("Report - Google Docs - Google Chrome")
    session = _session(desk)

    started = await focus_reflex(
        "Jarvis, thirty minutes on the quarterly report please", session=session
    )
    assert started is not None and "30 minutes" in started
    assert session.goal == "the quarterly report"

    desk.title = "Slides - Google Slides - Google Chrome"
    moved = await focus_reflex("lock on this tab", session=session)
    assert moved == "Locked on Slides - Google Slides."

    stopped = await focus_reflex("stop focus", session=session)
    assert stopped is not None and stopped.startswith("Focus ended after")
    assert not session.active


@pytest.mark.asyncio
async def test_ordinary_requests_are_left_to_the_brain() -> None:
    session = _session(_Desk("Spotify"))
    for text in (
        "focus on Chrome",
        "what time is it",
        "work",
        "open youtube",
        "how do I focus better?",
    ):
        assert await focus_reflex(text, session=session) is None, text
    assert not session.active


def test_diag_reports_booleans_only() -> None:
    desk = _Desk("Secret plans - Google Chrome")
    report = _session(desk).diag()

    assert all(isinstance(value, bool) for value in report.values())
    assert report["foreground_is_browser"] is True
    assert "Secret" not in repr(report)
