"""Project health: the one rule table behind the Orb's project colours.

``status`` (``status.py`` / ``engine.rollup``) says what a project's own
records state. Health answers the question a person asks at a glance, "how far
along is this and is it moving?", from that status plus measured progress:

* **red** — blocked, failed, or incomplete with little progress and idle;
* **yellow** — partial: open work remains (in progress, waiting, paused);
* **blue** — active (being discussed, nothing tracked open) or under review
  (every tracked item done, nothing has verified the project yet);
* **green** — verified / completed: the project's own status is green and
  nothing tracked is open.

Progress counts tasks and blockers only. An item is done only when the rules
gave it ``green``, which needs completion evidence (a later "shipped" or
"unblocked", an approved mission, a ``/goal`` marked complete): nothing is
counted done because it looks finished or was mentioned once. Milestones,
decisions and conversations are context, not work, so they never move the bar.

The table is ordered; the first rule that holds decides. It is served with
every project list (``HEALTH_RULES``) so the Orb's legend is this table, not a
copy of it, and ``tests/unit/memory/brain_map/test_health.py`` pins every row.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any, Final, Literal

from .constants import ACTIVE_WINDOW_MS, CONFIDENCE_FLOOR
from .status import day

HealthColour = Literal["red", "yellow", "blue", "green"]
HealthRuleId = Literal[
    "blocked", "failed", "verified", "stalled", "partial", "review", "active", "idle"
]

#: Below this share of tracked work done, an idle project counts as stalled.
LITTLE_PROGRESS: Final[float] = 0.25

#: Kinds that are work: they can be open or done and they move the bar.
WORK_KINDS: Final[tuple[str, ...]] = ("TASK", "BLOCKER")


@dataclass(frozen=True, slots=True)
class HealthRule:
    id: str
    colour: str
    when: str


#: The rule table, in the order it is evaluated.
HEALTH_RULES: Final[tuple[HealthRule, ...]] = (
    HealthRule("blocked", "red", "An open blocker that is not just waiting on someone."),
    HealthRule(
        "failed", "red", "The project's own status is red: a failed, timed-out or overdue item."
    ),
    HealthRule(
        "verified",
        "green",
        "The project's status is green with evidence and nothing tracked is open.",
    ),
    HealthRule(
        "stalled",
        "red",
        "Work is open, under a quarter of it is done, and nothing happened in the last 7 days.",
    ),
    HealthRule("partial", "yellow", "Work is still open: in progress, waiting or paused."),
    HealthRule(
        "review", "blue", "Every tracked item is done, but nothing has verified the project yet."
    ),
    HealthRule("active", "blue", "Touched in the last 7 days with no tracked work open."),
    HealthRule("idle", "yellow", "Nothing open, nothing verified and no recent activity."),
)
HEALTH_RULE_IDS: Final[tuple[str, ...]] = tuple(r.id for r in HEALTH_RULES)
HEALTH_COLOURS: Final[tuple[str, ...]] = ("red", "yellow", "blue", "green")


@dataclass(frozen=True, slots=True)
class WorkItem:
    """One tracked task or blocker, as far as health is concerned."""

    kind: str
    status: str


@dataclass(frozen=True, slots=True)
class Health:
    colour: str
    rule: str
    reason: str
    #: Done share of tracked work, 0..1; ``None`` when nothing is tracked.
    progress: float | None
    done: int
    total: int
    open_blockers: int
    open_tasks: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def project_health(
    *,
    status: str,
    status_reason: str,
    last_activity_ms: int,
    work: Iterable[WorkItem],
    now_ms: int,
) -> Health:
    """Evaluate ``HEALTH_RULES`` for one project. Pure; no I/O, no clock."""
    items = [w for w in work if w.kind in WORK_KINDS]
    total = len(items)
    done = sum(1 for w in items if w.status == "green")
    blocked = sum(1 for w in items if w.kind == "BLOCKER" and w.status == "red")
    open_blockers = sum(1 for w in items if w.kind == "BLOCKER" and w.status != "green")
    open_tasks = sum(1 for w in items if w.kind == "TASK" and w.status != "green")
    open_work = total - done
    progress = round(done / total, 4) if total else None
    idle = last_activity_ms <= 0 or now_ms - last_activity_ms > ACTIVE_WINDOW_MS
    tally = f"{done} of {_plural(total, 'item')} done" if total else "no tracked work"

    def verdict(rule: str, reason: str) -> Health:
        colour = next(r.colour for r in HEALTH_RULES if r.id == rule)
        return Health(colour, rule, reason, progress, done, total, open_blockers, open_tasks)

    if blocked:
        return verdict("blocked", f"blocked: {_plural(blocked, 'open blocker')}; {tally}")
    if status == "red":
        return verdict("failed", status_reason or "a tracked item failed")
    if status == "green" and open_work == 0:
        return verdict("verified", f"verified: {status_reason}" if status_reason else "verified")
    if open_work and (progress or 0.0) < LITTLE_PROGRESS and idle:
        since = (
            f"no activity since {day(last_activity_ms)}"
            if last_activity_ms > 0
            else "no activity recorded"
        )
        return verdict("stalled", f"incomplete: {tally}; {since}")
    if open_work:
        return verdict("partial", f"in progress: {tally}")
    if total:
        return verdict(
            "review", f"under review: all {_plural(total, 'item')} done, not verified yet"
        )
    if not idle:
        return verdict("active", f"active: last activity {day(last_activity_ms)}")
    since = day(last_activity_ms) if last_activity_ms > 0 else "never"
    return verdict("idle", f"idle: nothing open or verified; last activity {since}")


def work_of(members: Iterable[Any]) -> list[WorkItem]:
    """Tracked work among a project's members (``engine.Member``).

    Only confirmed members at or above the confidence floor count, the same
    set the status rollup reads; a suggested chat's tasks are shown, hollow,
    but never move a project's colour.
    """
    return [
        WorkItem(str(m.row["kind"]), str(m.row["status"]))
        for m in members
        if m.assignment == "confirmed"
        and m.confidence >= CONFIDENCE_FLOOR
        and m.row["kind"] in WORK_KINDS
    ]


def rules_payload() -> list[dict[str, str]]:
    return [{"id": r.id, "colour": r.colour, "when": r.when} for r in HEALTH_RULES]


__all__ = [
    "HEALTH_COLOURS",
    "HEALTH_RULES",
    "HEALTH_RULE_IDS",
    "LITTLE_PROGRESS",
    "WORK_KINDS",
    "Health",
    "HealthColour",
    "HealthRule",
    "HealthRuleId",
    "WorkItem",
    "project_health",
    "rules_payload",
    "work_of",
]
