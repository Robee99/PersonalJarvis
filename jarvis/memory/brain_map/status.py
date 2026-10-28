"""Status rules: every native vocabulary mapped onto red / blue / yellow / green.

Pure functions, no I/O. Each returns a :class:`Verdict` with a short reason a
person can read ("mission failed", "stale since 2026-09-30") and, when the
verdict depends on the clock, ``review_at_ms``: the instant it would change by
itself. The indexer re-derives exactly the rows whose instant has passed, so
"no activity for N days" turns yellow without re-reading anything else.

The colours:

* **red** — blocked, failed, or overdue and undone;
* **blue** — active;
* **yellow** — waiting, stale, or needs a look;
* **green** — done, verified, or closed with nothing left to do.

A model never sets any of this; a model may only propose an entity with a
quote (``extractor.py``), and its status is then derived here like any other.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .constants import (
    ACTIVE_WINDOW_MS,
    STALE_BLOCKER_MS,
    STALE_ITEM_MS,
    STALE_PROJECT_MS,
)


@dataclass(frozen=True, slots=True)
class Verdict:
    status: str
    reason: str
    confidence: float = 1.0
    review_at_ms: int | None = None


def day(ms: int) -> str:
    """``YYYY-MM-DD`` (UTC) for a millisecond timestamp."""
    if ms <= 0:
        return "unknown date"
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _active_or_stale(
    label: str, last_ms: int, now_ms: int, window_ms: int = STALE_ITEM_MS
) -> Verdict:
    """Blue while touched within ``window_ms``, then yellow "stale since"."""
    if last_ms <= 0:
        return Verdict("yellow", f"{label}; no recorded activity")
    if now_ms - last_ms > window_ms:
        return Verdict("yellow", f"{label}; stale since {day(last_ms)}")
    return Verdict("blue", label, review_at_ms=last_ms + window_ms + 1)


def mission_status(state: str, updated_ms: int, now_ms: int) -> Verdict:
    """``MissionState`` (jarvis/missions/state_machine.py)."""
    value = (state or "").upper()
    if value in ("PENDING", "RUNNING", "CRITIQUING", "LOOPING"):
        return _active_or_stale(f"mission {value.lower()}", updated_ms, now_ms)
    if value == "APPROVED":
        return Verdict("green", "mission approved")
    if value == "FAILED":
        return Verdict("red", "mission failed")
    if value == "TIMED_OUT":
        return Verdict("red", "mission timed out")
    if value == "CANCELLED":
        return Verdict("green", "mission cancelled; nothing left to do")
    return Verdict("yellow", f"mission in unknown state {value or 'none'}", confidence=0.5)


def quest_status(state: str, updated_ms: int, now_ms: int) -> Verdict:
    """``society_quests.state`` (jarvis/society/society_schema.sql)."""
    value = (state or "").lower()
    if value == "open":
        return Verdict("yellow", "quest waiting for a taker")
    if value in ("assigned", "running"):
        return _active_or_stale(f"quest {value}", updated_ms, now_ms)
    if value == "done":
        return Verdict("green", "quest done")
    if value == "failed":
        return Verdict("red", "quest failed")
    if value == "cancelled":
        return Verdict("green", "quest cancelled; nothing left to do")
    return Verdict("yellow", f"quest in unknown state {value or 'none'}", confidence=0.5)


def goal_status(status: str, updated_ms: int, now_ms: int) -> Verdict:
    """``GoalStatus`` of a chat ``/goal`` (jarvis/agent_chat/control_types.py)."""
    value = (status or "").lower()
    if value == "active":
        return _active_or_stale("goal active", updated_ms, now_ms)
    if value == "paused":
        return Verdict("yellow", f"goal paused since {day(updated_ms)}")
    if value == "blocked":
        return Verdict("red", "goal blocked")
    if value == "complete":
        return Verdict("green", "goal complete")
    if value == "cleared":
        return Verdict("green", "goal cleared; nothing left to do")
    return Verdict("yellow", f"goal in unknown state {value or 'none'}", confidence=0.5)


def automation_status(state: str, due_ms: int | None, updated_ms: int, now_ms: int) -> Verdict:
    """``tasks.state`` of an automation / reminder (jarvis/tasks/schema.sql)."""
    value = (state or "").lower()
    if value in ("pending", "scheduled"):
        if due_ms and due_ms < now_ms:
            return Verdict("red", f"automation overdue since {day(due_ms)}")
        if due_ms:
            return Verdict(
                "blue", f"automation scheduled for {day(due_ms)}", review_at_ms=due_ms + 1
            )
        return Verdict("blue", "automation waiting for its trigger")
    if value == "running":
        return Verdict("blue", "automation running")
    if value == "paused":
        return Verdict("yellow", f"automation paused since {day(updated_ms)}")
    if value == "completed":
        return Verdict("green", "automation completed")
    if value == "failed":
        return Verdict("red", "automation failed")
    if value == "interrupted":
        return Verdict("red", "automation interrupted")
    if value == "cancelled":
        return Verdict("green", "automation cancelled; nothing left to do")
    return Verdict("yellow", f"automation in unknown state {value or 'none'}", confidence=0.5)


def output_status(mission_state: str | None, mtime_ms: int, now_ms: int) -> Verdict:
    """A mission's output folder takes its mission's outcome."""
    if mission_state is None:
        return Verdict("yellow", "output with no known mission; needs review", confidence=0.6)
    value = mission_state.upper()
    if value == "APPROVED":
        return Verdict("green", "output delivered")
    if value in ("FAILED", "TIMED_OUT"):
        return Verdict("red", "output of a failed mission")
    if value == "CANCELLED":
        return Verdict("green", "output of a cancelled mission")
    return _active_or_stale("output in progress", mtime_ms, now_ms)


def ide_project_status(archived: bool, last_opened_ms: int, now_ms: int) -> Verdict:
    if archived:
        return Verdict("green", "project archived")
    return _active_or_stale("project open in the IDE", last_opened_ms, now_ms, STALE_PROJECT_MS)


def thread_status(label: str, archived: bool, updated_ms: int, now_ms: int) -> Verdict:
    if archived:
        return Verdict("green", f"{label} archived")
    if now_ms - updated_ms > STALE_ITEM_MS and updated_ms > 0:
        # A quiet chat is not a problem by itself; only open items make it one
        # (the project rollup sees those directly).
        return Verdict("green", f"{label} quiet since {day(updated_ms)}")
    return _active_or_stale(label, updated_ms, now_ms)


def wiki_project_status(status: str, updated_ms: int, now_ms: int) -> Verdict:
    """``status`` frontmatter of a wiki project page (W/templates/schema.md)."""
    value = (status or "").lower()
    if value == "active":
        return _active_or_stale("wiki project active", updated_ms, now_ms, STALE_PROJECT_MS)
    if value == "paused":
        return Verdict("yellow", "wiki project paused")
    if value == "done":
        return Verdict("green", "wiki project done")
    if value == "archived":
        return Verdict("green", "wiki project archived")
    return Verdict("yellow", f"wiki project status {value or 'missing'}", confidence=0.6)


def conversation_status(
    *,
    last_activity_ms: int,
    last_turn_failed: bool,
    awaiting_approval: bool,
    now_ms: int,
) -> Verdict:
    """A chat: its own state only; its open tasks and blockers stand alone."""
    if awaiting_approval:
        return Verdict("yellow", "conversation waiting for an approval")
    if last_turn_failed:
        return Verdict("red", "the last turn failed")
    return thread_status("conversation", False, last_activity_ms, now_ms)


def extracted_status(
    kind: str,
    *,
    last_mention_ms: int,
    signal: str,
    resolved_reason: str,
    now_ms: int,
) -> Verdict:
    """Status of something said in a conversation.

    ``signal`` is the rule that found it (``blocked`` / ``waiting`` for a
    blocker); ``resolved_reason`` is non-empty once completion evidence exists
    (a later "shipped", "unblocked"...). Done needs that evidence: nothing turns
    green because it was merely said once.
    """
    if resolved_reason:
        return Verdict("green", resolved_reason)
    if kind == "DECISION":
        return Verdict("green", f"decided on {day(last_mention_ms)}")
    if kind == "MILESTONE":
        return Verdict("green", f"reached on {day(last_mention_ms)}")
    if kind == "BLOCKER":
        if now_ms - last_mention_ms > STALE_BLOCKER_MS:
            return Verdict(
                "yellow",
                f"blocker not mentioned since {day(last_mention_ms)}; check it still holds",
            )
        review = last_mention_ms + STALE_BLOCKER_MS + 1
        if signal == "waiting":
            return Verdict("yellow", "waiting on an outside dependency", review_at_ms=review)
        return Verdict("red", "open blocker", review_at_ms=review)
    if kind == "TASK":
        if now_ms - last_mention_ms > STALE_ITEM_MS:
            return Verdict("yellow", f"open task; stale since {day(last_mention_ms)}")
        return Verdict("blue", "open task", review_at_ms=last_mention_ms + ACTIVE_WINDOW_MS + 1)
    return Verdict("blue", "mentioned")


__all__ = [
    "Verdict",
    "automation_status",
    "conversation_status",
    "day",
    "extracted_status",
    "goal_status",
    "ide_project_status",
    "mission_status",
    "output_status",
    "quest_status",
    "thread_status",
    "wiki_project_status",
]
