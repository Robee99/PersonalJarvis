"""Graph walks, the project rollup, and the priority score.

Pure Python over the brain map tables; nothing here calls a model (audit
§8.3: "the model never ranks").

**Membership.** A project's members are found by walking edges DOWN from it:
``x part_of P``; then whatever is ``part_of`` / ``decided_in`` / ``blocks`` a
member conversation or thread (its tasks, decisions, blockers); then the
outputs a member mission ``produced``. Nothing is copied onto the project: a
chat moving to another project moves its decisions with it.

**Priority** is a deterministic score with a one-sentence reason:

* status — red 50, yellow 30, blue 20, green 0 (green has nothing to do);
* open blockers — +8 each, at most +32;
* staleness — a stale yellow item gains up to +15 with age;
* recency — touched within 3 days +10, within 7 days +5;
* explicit priority — +10 per level (urgency words, a wiki ``priority:``);
* the whole score scales with the entity's confidence.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from .constants import ACTIVE_WINDOW_MS, CONFIDENCE_FLOOR, DAY_MS
from .status import Verdict, day, ide_project_status, wiki_project_status

_UP_KINDS = ("part_of", "decided_in", "blocks")
_STATUS_WEIGHT = {"red": 50.0, "yellow": 30.0, "blue": 20.0, "green": 0.0}
_OPEN = ("red", "yellow")


@dataclass(frozen=True, slots=True)
class Member:
    row: sqlite3.Row
    confidence: float
    assignment: str  # confirmed | suggested
    via: str  # the entity it hangs off (the project itself for direct members)


def projects_of(conn: sqlite3.Connection, entity_id: str, depth: int = 4) -> set[str]:
    """Every PROJECT reachable upwards from ``entity_id`` (suggested edges too)."""
    found: set[str] = set()
    seen: set[str] = set()
    frontier = {entity_id}
    for _ in range(depth):
        nxt: set[str] = set()
        for node in frontier:
            seen.add(node)
            kind = conn.execute(
                "SELECT kind FROM brain_map_entities WHERE entity_id=?", (node,)
            ).fetchone()
            if kind is not None and kind[0] == "PROJECT":
                found.add(node)
                continue
            for (dst,) in conn.execute(
                "SELECT dst_id FROM brain_map_relations WHERE src_id=? AND kind IN (?, ?, ?)",
                (node, *_UP_KINDS),
            ):
                nxt.add(str(dst))
            for (src,) in conn.execute(
                "SELECT src_id FROM brain_map_relations WHERE dst_id=? AND kind='produced'",
                (node,),
            ):
                nxt.add(str(src))
        frontier = nxt - seen
        if not frontier:
            break
    return found


def members_of(conn: sqlite3.Connection, project_id: str, depth: int = 3) -> list[Member]:
    """Every entity in a project's cluster, with the weakest link's confidence."""
    out: dict[str, Member] = {}
    frontier: list[tuple[str, float, str]] = [(project_id, 1.0, "confirmed")]
    for level in range(depth):
        nxt: list[tuple[str, float, str]] = []
        for node, conf, assign in frontier:
            kinds = ("part_of",) if level == 0 else _UP_KINDS
            rows = conn.execute(
                "SELECT r.src_id, r.confidence, r.assignment FROM brain_map_relations r"  # noqa: S608 — only '?' placeholders and fixed column names are interpolated
                f" WHERE r.dst_id=? AND r.kind IN ({', '.join('?' for _ in kinds)})",
                (node, *kinds),
            ).fetchall()
            rows += (
                conn.execute(
                    "SELECT dst_id, confidence, assignment FROM brain_map_relations"
                    " WHERE src_id=? AND kind='produced'",
                    (node,),
                ).fetchall()
                if level > 0
                else []
            )
            for child, c_conf, c_assign in rows:
                child = str(child)
                if child == project_id or child in out:
                    continue
                row = conn.execute(
                    "SELECT * FROM brain_map_entities WHERE entity_id=?", (child,)
                ).fetchone()
                if row is None or row["kind"] == "PROJECT":
                    continue
                confidence = min(conf, float(c_conf))
                assignment = "suggested" if "suggested" in (assign, c_assign) else "confirmed"
                out[child] = Member(row, confidence, assignment, node)
                nxt.append((child, confidence, assignment))
        frontier = nxt
        if not frontier:
            break
    return list(out.values())


def agents_of(conn: sqlite3.Connection, entity_ids: list[str]) -> list[sqlite3.Row]:
    if not entity_ids:
        return []
    marks = ", ".join("?" for _ in entity_ids)
    return list(
        conn.execute(
            "SELECT DISTINCT e.* FROM brain_map_relations r JOIN brain_map_entities e"  # noqa: S608 — only '?' placeholders and fixed column names are interpolated
            f" ON e.entity_id = r.dst_id WHERE r.kind='mentions' AND r.src_id IN ({marks})"
            " AND e.kind='AGENT'",
            entity_ids,
        )
    )


def open_blockers(conn: sqlite3.Connection, entity_id: str) -> list[sqlite3.Row]:
    """Open blockers that ``blocks`` this entity directly."""
    return list(
        conn.execute(
            "SELECT e.* FROM brain_map_relations r JOIN brain_map_entities e"
            " ON e.entity_id = r.src_id WHERE r.dst_id=? AND r.kind='blocks'"
            " AND e.kind='BLOCKER' AND e.status IN ('red', 'yellow')",
            (entity_id,),
        )
    )


def own_verdict(conn: sqlite3.Connection, project: sqlite3.Row, now: int) -> Verdict | None:
    """The status the project's own defining record states (IDE / wiki page)."""
    kind = str(project["source_kind"] or "")
    if not kind:
        return None
    src = conn.execute(
        "SELECT last_activity_ms FROM brain_map_sources WHERE source_kind=? AND source_id=?",
        (kind, project["source_id"]),
    ).fetchone()
    last = int(src[0]) if src is not None else 0
    native = str(project["native_status"] or "")
    if kind == "ide_project":
        return ide_project_status(native == "archived", last, now)
    if kind == "wiki_project":
        return wiki_project_status(native, last, now)
    return None


def _quoted(title: str, limit: int = 70) -> str:
    text = title if len(title) <= limit else title[: limit - 1].rstrip() + "…"
    return f"“{text}”"


@dataclass(frozen=True, slots=True)
class Rollup:
    verdict: Verdict
    last_activity_ms: int
    summary: str
    input_hash: str
    open_blockers: int


def rollup(
    project: sqlite3.Row, own: Verdict | None, members: list[Member], own_last: int
) -> Rollup:
    """A project's status from its confirmed members and its own record."""
    confirmed = [
        m
        for m in members
        if m.assignment == "confirmed"
        and m.confidence >= CONFIDENCE_FLOOR
        and m.row["kind"] != "AGENT"
    ]
    rows = [m.row for m in confirmed]
    last = max([own_last, *(int(r["last_activity_ms"] or 0) for r in rows)], default=0)
    reds = sorted(
        (r for r in rows if r["status"] == "red"),
        key=lambda r: (r["kind"] != "BLOCKER", -float(r["priority_score"] or 0)),
    )
    blues = [r for r in rows if r["status"] == "blue"]
    yellows = sorted(
        (r for r in rows if r["status"] == "yellow"),
        key=lambda r: (r["kind"] != "BLOCKER", -float(r["priority_score"] or 0)),
    )
    blockers = [r for r in rows if r["kind"] == "BLOCKER" and r["status"] in _OPEN]
    if reds:
        first = reds[0]
        more = f" (+{len(reds) - 1} more)" if len(reds) > 1 else ""
        verdict = Verdict(
            "red",
            f"{first['kind'].lower()} {_quoted(first['title'])}: {first['status_reason']}{more}",
        )
    elif own is not None and own.status == "green" and not blues:
        verdict = own
    elif blues or (own is not None and own.status == "blue"):
        count = len(blues)
        verdict = Verdict(
            "blue",
            f"active: {count} item{'s' if count != 1 else ''} in progress;"
            f" last activity {day(last)}",
        )
    elif yellows:
        first = yellows[0]
        verdict = Verdict(
            "yellow", f"needs attention: {_quoted(first['title'])} ({first['status_reason']})"
        )
    elif own is not None and own.status == "yellow":
        verdict = own
    elif rows:
        verdict = Verdict("green", f"all {len(rows)} items done or closed")
    else:
        verdict = Verdict("yellow", "no activity recorded yet", confidence=0.6)
    counts = {
        k: sum(1 for r in rows if r["kind"] == k)
        for k in ("CONVERSATION", "THREAD", "TASK", "DECISION", "BLOCKER", "MILESTONE", "OUTPUT")
    }
    open_tasks = sum(
        1 for r in rows if r["kind"] == "TASK" and r["status"] in ("red", "blue", "yellow")
    )
    decisions = sorted(
        (r for r in rows if r["kind"] == "DECISION"),
        key=lambda r: -int(r["last_activity_ms"] or 0),
    )
    parts = [
        f"{project['title']} is {verdict.status}: {verdict.reason}.",
        f"{counts['CONVERSATION'] + counts['THREAD']} conversations, {open_tasks} open tasks,"
        f" {len(blockers)} open blockers, {counts['DECISION']} decisions,"
        f" {counts['OUTPUT']} outputs.",
    ]
    if decisions:
        parts.append(f"Latest decision: {_quoted(decisions[0]['title'], 100)}.")
    if last:
        parts.append(f"Last activity {day(last)}.")
    digest = hashlib.sha256()
    for m in sorted(members, key=lambda m: m.row["entity_id"]):
        digest.update(
            f"{m.row['entity_id']}|{m.row['status']}|{m.row['last_activity_ms']}"
            f"|{m.assignment}|{m.confidence}\n".encode()
        )
    digest.update(f"own|{own.status if own else ''}|{own_last}".encode())
    return Rollup(verdict, last, " ".join(parts), digest.hexdigest()[:32], len(blockers))


def priority(
    row: sqlite3.Row | dict,
    *,
    now: int,
    blockers: list[sqlite3.Row] | None = None,
    open_blocker_count: int | None = None,
) -> tuple[float, str]:
    """Deterministic score + a short human sentence explaining it."""
    status = str(row["status"])
    if status == "green":
        return 0.0, "Nothing to do: " + str(row["status_reason"] or "done") + "."
    score = _STATUS_WEIGHT.get(status, 0.0)
    clauses: list[str] = [f"{status.capitalize()}: {row['status_reason']}"]
    count = open_blocker_count if open_blocker_count is not None else len(blockers or [])
    if count:
        score += min(32.0, 8.0 * count)
        named = f" ({_quoted(str(blockers[0]['title']), 50)})" if blockers else ""
        clauses.append(f"{count} open blocker{'s' if count != 1 else ''}{named}")
    last = int(row["last_activity_ms"] or 0)
    age = now - last if last else None
    if status == "yellow" and age is not None and age > ACTIVE_WINDOW_MS:
        score += min(15.0, (age / DAY_MS) * 0.5)
    if age is not None and age <= 3 * DAY_MS:
        score += 10.0
        clauses.append("touched in the last 3 days")
    elif age is not None and age <= ACTIVE_WINDOW_MS:
        score += 5.0
        clauses.append("touched this week")
    elif last:
        clauses.append(f"last touched {day(last)}")
    explicit = int(row["user_priority"] or 0)
    if explicit:
        score += 10.0 * explicit
        clauses.append("marked urgent" if explicit >= 3 else "marked important")
    confidence = float(row["confidence"] if row["confidence"] is not None else 1.0)
    score *= max(0.2, min(1.0, confidence))
    if confidence < CONFIDENCE_FLOOR:
        clauses.append("low confidence")
    return round(score, 2), "; ".join(clauses) + "."


__all__ = [
    "Member",
    "Rollup",
    "agents_of",
    "members_of",
    "open_blockers",
    "own_verdict",
    "priority",
    "projects_of",
    "rollup",
]
