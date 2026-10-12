"""Read model over the brain map: what the REST routes and MCP tools return.

Plain dicts (JSON-ready). Nothing here writes; every answer is computed from
the derived tables, and source text is never re-read except the bounded quote
stored as evidence.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from .constants import CONFIDENCE_FLOOR
from .engine import agents_of, members_of
from .health import project_health, rules_payload, work_of
from .sources import conversation_id_of, git_branch
from .status import day
from .store import BrainMapStore
from .thread import build_thread

#: Default colours per status for a consumer without its own theme tokens.
STATUS_COLOURS: dict[str, str] = {
    "red": "#e5484d",
    "blue": "#3e63dd",
    "yellow": "#f5a524",
    "green": "#30a46c",
}

_OPEN = ("red", "blue", "yellow")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso_day(ms: int) -> str:
    return day(ms) if ms else ""


def source_jump(source_kind: str, source_id: str, locator: str = "") -> dict[str, Any]:
    """Where a person (or the Orb) goes to see the source row itself."""
    api: str | None = None
    conversation_id = ""
    if source_kind == "agent_chat":
        api, conversation_id = f"/api/agent-chat/sessions/{source_id}", source_id
    elif source_kind == "goal":
        session = source_id.rsplit(":", 1)[0]
        api, conversation_id = f"/api/agent-chat/sessions/{session}/control", session
    elif source_kind == "mission":
        api = f"/api/missions/{source_id}"
    elif source_kind == "output":
        api = f"/api/outputs/{source_id}/artifacts"
    elif source_kind == "quest":
        api = f"/api/society/quests/{source_id}"
    elif source_kind == "automation":
        api = f"/api/tasks/{source_id}"
    elif source_kind == "wiki_project":
        api = f"/api/wiki/page/{Path(source_id).stem}"
    elif source_kind in ("wiki_import", "cli_session"):
        conversation_id = conversation_id_of(source_kind, source_id)
    return {
        "source_kind": source_kind,
        "source_id": source_id,
        "conversation_id": conversation_id,
        "api": api,
        "locator": locator,
    }


def _entity(
    row: sqlite3.Row, *, assignment: str = "confirmed", confidence: float | None = None
) -> dict[str, Any]:
    return {
        "id": row["entity_id"],
        "kind": row["kind"],
        "title": row["title"],
        "status": row["status"],
        "status_reason": row["status_reason"],
        "confidence": round(float(confidence if confidence is not None else row["confidence"]), 3),
        "origin": row["origin"],
        "last_activity_ms": int(row["last_activity_ms"] or 0),
        "last_updated_ms": int(row["last_updated_ms"] or 0),
        "priority_score": float(row["priority_score"] or 0.0),
        "priority_reason": row["priority_reason"],
        "assignment": assignment,
        "source": source_jump(str(row["source_kind"]), str(row["source_id"]), str(row["locator"])),
    }


def _project_row(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row | None:
    row = BrainMapStore.get_entity(conn, project_id)
    return row if row is not None and row["kind"] == "PROJECT" else None


def _counts(members: list[Any]) -> dict[str, int]:
    rows = [
        m.row for m in members if m.assignment == "confirmed" and m.confidence >= CONFIDENCE_FLOOR
    ]
    return {
        "conversations": sum(1 for r in rows if r["kind"] in ("CONVERSATION", "THREAD")),
        "open_tasks": sum(1 for r in rows if r["kind"] == "TASK" and r["status"] in _OPEN),
        "open_blockers": sum(
            1 for r in rows if r["kind"] == "BLOCKER" and r["status"] in ("red", "yellow")
        ),
        "decisions": sum(1 for r in rows if r["kind"] == "DECISION"),
        "suggested": sum(
            1 for m in members if m.assignment == "suggested" or m.confidence < CONFIDENCE_FLOOR
        ),
    }


def _health(row: sqlite3.Row, members: list[Any], now_ms: int) -> dict[str, Any]:
    return project_health(
        status=str(row["status"]),
        status_reason=str(row["status_reason"] or ""),
        last_activity_ms=int(row["last_activity_ms"] or 0),
        work=work_of(members),
        now_ms=now_ms,
    ).to_dict()


def _project_summary(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    now_ms: int,
    members: list[Any] | None = None,
) -> dict[str, Any]:
    if members is None:
        members = members_of(conn, str(row["entity_id"]))
    out = _entity(row)
    out.update(
        summary=row["summary"],
        counts=_counts(members),
        locator=row["locator"],
        health=_health(row, members, now_ms),
    )
    return out


def health_rules() -> list[dict[str, str]]:
    """The ordered health rule table (``health.HEALTH_RULES``), JSON-ready."""
    return rules_payload()


def list_projects(store: BrainMapStore, *, now_ms: int | None = None) -> list[dict[str, Any]]:
    now = _now_ms() if now_ms is None else now_ms
    with store.read() as conn:
        rows = conn.execute(
            "SELECT * FROM brain_map_entities WHERE kind='PROJECT'"
            " ORDER BY priority_score DESC, last_activity_ms DESC"
        ).fetchall()
        return [_project_summary(conn, r, now) for r in rows]


def next_actions(project: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    """Deterministic next steps: blockers first, then failures, then open work."""
    actions: list[dict[str, Any]] = []
    for item in project["blockers"]:
        if item["status"] == "red":
            actions.append(
                {
                    "action": f"Resolve blocker: {item['title']}",
                    "entity_id": item["id"],
                    "why": item["priority_reason"],
                }
            )
    for item in project["tasks"]:
        if item["status"] == "red":
            actions.append(
                {
                    "action": f"Look into: {item['title']} ({item['status_reason']})",
                    "entity_id": item["id"],
                    "why": item["priority_reason"],
                }
            )
    for item in project["blockers"]:
        if item["status"] == "yellow":
            actions.append(
                {
                    "action": f"Check on: {item['title']}",
                    "entity_id": item["id"],
                    "why": item["priority_reason"],
                }
            )
    for item in project["tasks"]:
        if item["status"] in ("blue", "yellow"):
            verb = "Revisit" if item["status"] == "yellow" else "Continue"
            actions.append(
                {
                    "action": f"{verb}: {item['title']}",
                    "entity_id": item["id"],
                    "why": item["priority_reason"],
                }
            )
    return actions[:limit]


def project_detail(
    store: BrainMapStore, project_id: str, *, now_ms: int | None = None
) -> dict[str, Any] | None:
    now = _now_ms() if now_ms is None else now_ms
    with store.read() as conn:
        row = _project_row(conn, project_id)
        if row is None:
            return None
        members = members_of(conn, project_id)
        out = _project_summary(conn, row, now, members)
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for m in sorted(members, key=lambda m: -float(m.row["priority_score"] or 0)):
            assignment = (
                "suggested"
                if m.assignment == "suggested" or m.confidence < CONFIDENCE_FLOOR
                else "confirmed"
            )
            by_kind.setdefault(str(m.row["kind"]), []).append(
                _entity(m.row, assignment=assignment, confidence=m.confidence)
            )
        thread = build_thread(
            project_id,
            (
                {
                    **_entity(
                        m.row,
                        assignment=(
                            "suggested"
                            if m.assignment == "suggested" or m.confidence < CONFIDENCE_FLOOR
                            else "confirmed"
                        ),
                        confidence=m.confidence,
                    ),
                    "via": m.via,
                    "first_seen_ms": int(m.row["first_seen_ms"] or 0),
                    "source_kind": str(m.row["source_kind"] or ""),
                }
                for m in members
            ),
        )
        ids = [str(m.row["entity_id"]) for m in members]
        timeline: list[dict[str, Any]] = []
        if ids:
            marks = ", ".join("?" for _ in ids + [project_id])
            for h in conn.execute(
                "SELECT h.entity_id, h.ts_ms, h.status, h.reason, e.title, e.kind"  # noqa: S608 — only '?' placeholders and fixed column names are interpolated
                " FROM brain_map_status_history h JOIN brain_map_entities e"
                " ON e.entity_id=h.entity_id"
                f" WHERE h.entity_id IN ({marks}) ORDER BY h.ts_ms DESC, h.id DESC LIMIT 100",
                [*ids, project_id],
            ):
                timeline.append(
                    {
                        "ts_ms": int(h["ts_ms"]),
                        "entity_id": h["entity_id"],
                        "kind": h["kind"],
                        "title": h["title"],
                        "status": h["status"],
                        "reason": h["reason"],
                        "event": "status",
                    }
                )
            for ev in conn.execute(
                "SELECT v.entity_id, MIN(v.ts_ms) AS ts, e.title, e.kind FROM brain_map_evidence v"  # noqa: S608 — only '?' placeholders and fixed column names are interpolated
                " JOIN brain_map_entities e ON e.entity_id=v.entity_id"
                f" WHERE v.entity_id IN ({', '.join('?' for _ in ids)})"
                " AND e.kind IN ('DECISION', 'MILESTONE', 'BLOCKER', 'TASK') GROUP BY v.entity_id",
                ids,
            ):
                timeline.append(
                    {
                        "ts_ms": int(ev["ts"]),
                        "entity_id": ev["entity_id"],
                        "kind": ev["kind"],
                        "title": ev["title"],
                        "status": None,
                        "reason": "said",
                        "event": "said",
                    }
                )
        timeline.sort(key=lambda t: -t["ts_ms"])
        files: list[dict[str, Any]] = []
        if row["locator"]:
            files.append({"kind": "folder", "path": row["locator"]})
        for m in members:
            if m.row["kind"] in ("DOCUMENT", "OUTPUT") and m.row["locator"]:
                files.append(
                    {
                        "kind": str(m.row["kind"]).lower(),
                        "path": m.row["locator"],
                        "entity_id": m.row["entity_id"],
                    }
                )
        branches: list[dict[str, Any]] = []
        paths = {row["locator"]} | {
            str(r["cwd"])
            for r in conn.execute(
                "SELECT DISTINCT s.cwd FROM brain_map_sources s JOIN brain_map_entities e"  # noqa: S608 — only '?' placeholders and fixed column names are interpolated
                " ON e.source_kind=s.source_kind AND e.source_id=s.source_id"
                f" WHERE e.entity_id IN ({', '.join('?' for _ in ids) or 'NULL'}) AND s.cwd <> ''",
                ids,
            )
        }
        for path in sorted(p for p in paths if p):
            branch = git_branch(path)
            if branch and not any(b["name"] == branch for b in branches):
                branches.append({"name": branch, "path": path, "source": "git HEAD"})
        agents = [_entity(a) for a in agents_of(conn, ids)]
    out.update(
        state={
            "status": out["status"],
            "status_reason": out["status_reason"],
            "confidence": out["confidence"],
            "last_updated_ms": out["last_updated_ms"],
            "last_activity_ms": out["last_activity_ms"],
        },
        timeline=timeline[:100],
        conversations=by_kind.get("CONVERSATION", []) + by_kind.get("THREAD", []),
        decisions=by_kind.get("DECISION", []),
        tasks=by_kind.get("TASK", []),
        blockers=by_kind.get("BLOCKER", []),
        milestones=by_kind.get("MILESTONE", []),
        files=files,
        branches=branches,
        agents=agents,
        reports=by_kind.get("OUTPUT", []) + by_kind.get("DOCUMENT", []),
        priority={"score": out["priority_score"], "reason": out["priority_reason"]},
        thread=thread,
    )
    out["next_actions"] = next_actions(out)
    return out


def graph(store: BrainMapStore, *, limit: int = 600, now_ms: int | None = None) -> dict[str, Any]:
    """Nodes + edges for the Orb. Highest priority / most recent first.

    Project nodes carry their health, so the Orb colours a project by the same
    rule table the project list uses.
    """
    now = _now_ms() if now_ms is None else now_ms
    with store.read() as conn:
        rows = conn.execute(
            "SELECT * FROM brain_map_entities ORDER BY (kind='PROJECT') DESC,"
            " priority_score DESC, last_activity_ms DESC LIMIT ?",
            (max(1, min(limit, 5000)),),
        ).fetchall()
        ids = {str(r["entity_id"]) for r in rows}
        suggested = {
            str(r[0])
            for r in conn.execute(
                "SELECT src_id FROM brain_map_relations"
                " WHERE kind='part_of' AND assignment='suggested'"
            )
        }
        confirmed = {
            str(r[0])
            for r in conn.execute(
                "SELECT src_id FROM brain_map_relations"
                " WHERE kind='part_of' AND assignment='confirmed'"
            )
        }
        edges = [
            {
                "source": r["src_id"],
                "target": r["dst_id"],
                "kind": r["kind"],
                "confidence": float(r["confidence"]),
                "assignment": r["assignment"],
            }
            for r in conn.execute("SELECT * FROM brain_map_relations")
            if r["src_id"] in ids and r["dst_id"] in ids
        ]
        healths = {
            str(r["entity_id"]): _health(r, members_of(conn, str(r["entity_id"])), now)
            for r in rows
            if r["kind"] == "PROJECT"
        }
    nodes = []
    for r in rows:
        node = _entity(r)
        node.update(
            color=STATUS_COLOURS.get(str(r["status"]), STATUS_COLOURS["blue"]),
            suggested=str(r["entity_id"]) in suggested and str(r["entity_id"]) not in confirmed,
            health=healths.get(str(r["entity_id"])),
        )
        nodes.append(node)
    return {
        "nodes": nodes,
        "edges": edges,
        "status_colours": STATUS_COLOURS,
        "health_rules": rules_payload(),
    }


def entity_detail(store: BrainMapStore, entity_id: str) -> dict[str, Any] | None:
    from .engine import projects_of

    with store.read() as conn:
        row = BrainMapStore.get_entity(conn, entity_id)
        if row is None:
            return None
        out = _entity(row)
        out["evidence"] = [
            {
                "quote": e["quote"],
                "char_start": int(e["char_start"]),
                "char_end": int(e["char_end"]),
                "confidence": float(e["confidence"]),
                "extractor": e["extractor"],
                "segment_id": e["segment_id"],
                "source": {
                    **source_jump(str(e["source_kind"]), str(e["source_id"])),
                    "conversation_id": e["conversation_id"],
                    "message_ref": e["message_ref"],
                    "message_id": e["message_id"],
                    "ts_ms": int(e["ts_ms"]),
                },
            }
            for e in BrainMapStore.evidence_for(conn, entity_id)
        ]
        out["relations"] = {
            "outgoing": [
                {
                    "kind": r["kind"],
                    "target": r["dst_id"],
                    "confidence": float(r["confidence"]),
                    "assignment": r["assignment"],
                    "basis": r["basis"],
                }
                for r in BrainMapStore.relations_from(conn, entity_id)
            ],
            "incoming": [
                {
                    "kind": r["kind"],
                    "source": r["src_id"],
                    "confidence": float(r["confidence"]),
                    "assignment": r["assignment"],
                    "basis": r["basis"],
                }
                for r in BrainMapStore.relations_to(conn, entity_id)
            ],
        }
        out["status_history"] = [
            {"ts_ms": int(h["ts_ms"]), "status": h["status"], "reason": h["reason"]}
            for h in conn.execute(
                "SELECT ts_ms, status, reason FROM brain_map_status_history WHERE entity_id=?"
                " ORDER BY ts_ms, id",
                (entity_id,),
            )
        ]
        out["projects"] = sorted(projects_of(conn, entity_id) - {entity_id})
        out["summary"] = row["summary"]
    return out


def items(
    store: BrainMapStore, kind: str, *, project_id: str | None = None, open_only: bool = True
) -> list[dict[str, Any]]:
    """Tasks or blockers, across all projects or one, highest priority first."""
    statuses = ("red", "yellow") if kind == "BLOCKER" else _OPEN
    with store.read() as conn:
        if project_id:
            members = members_of(conn, project_id)
            rows = [(m.row, m.assignment, m.confidence) for m in members if m.row["kind"] == kind]
        else:
            rows = [
                (r, "confirmed", None)
                for r in conn.execute("SELECT * FROM brain_map_entities WHERE kind=?", (kind,))
            ]
        out = [
            _entity(r, assignment=a, confidence=c)
            for r, a, c in rows
            if not open_only or r["status"] in statuses
        ]
    out.sort(key=lambda e: -e["priority_score"])
    return out


def find_source(store: BrainMapStore, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Entities whose title or evidence matches ``query``, with their source jump."""
    needle = " ".join((query or "").split())
    if not needle:
        return []
    like = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with store.read() as conn:
        rows = conn.execute(
            "SELECT DISTINCT e.* FROM brain_map_entities e LEFT JOIN brain_map_evidence v"
            " ON v.entity_id = e.entity_id WHERE e.title LIKE ? ESCAPE '\\'"
            " OR v.quote LIKE ? ESCAPE '\\' ORDER BY e.priority_score DESC,"
            " e.last_activity_ms DESC LIMIT ?",
            (like, like, max(1, min(limit, 50))),
        ).fetchall()
        ids = [str(r["entity_id"]) for r in rows]
    found = []
    for entity_id in ids:
        detail = entity_detail(store, entity_id)
        if detail is not None:
            found.append(detail)
    return found


def resolve_project(store: BrainMapStore, ref: str) -> str | None:
    """A project id from an id or a (part of a) title."""
    ref = (ref or "").strip()
    if not ref:
        return None
    with store.read() as conn:
        if _project_row(conn, ref) is not None:
            return ref
        rows = conn.execute(
            "SELECT entity_id, title FROM brain_map_entities WHERE kind='PROJECT'"
            " ORDER BY priority_score DESC"
        ).fetchall()
    lowered = ref.lower()
    exact = [r for r in rows if str(r["title"]).lower() == lowered]
    if exact:
        return str(exact[0]["entity_id"])
    partial = [r for r in rows if lowered in str(r["title"]).lower()]
    return str(partial[0]["entity_id"]) if partial else None


def _lines(items_: list[dict[str, Any]], empty: str, limit: int = 10) -> list[str]:
    if not items_:
        return [f"- {empty}"]
    return [
        f"- [{i['status'].upper()}] {i['title']} — {i['status_reason']}"
        + (" (suggested)" if i.get("assignment") == "suggested" else "")
        for i in items_[:limit]
    ]


def make_report(store: BrainMapStore, project_id: str) -> str | None:
    """A plain-text project report in the fixed section order."""
    detail = project_detail(store, project_id)
    if detail is None:
        return None
    refs: list[str] = []
    seen: set[str] = set()
    for group in ("decisions", "blockers", "tasks"):
        for item in detail[group][:10]:
            ent = entity_detail(store, item["id"])
            for ev in (ent or {}).get("evidence", [])[:2]:
                src = ev["source"]
                key = f"{src['source_kind']}:{src['conversation_id']}:{src['message_ref']}"
                if key in seen:
                    continue
                seen.add(key)
                when = _iso_day(src["ts_ms"])
                refs.append(
                    f"- {item['title']}: {src['source_kind']}"
                    f" {src['conversation_id'] or src['source_id']}"
                    f" {src['message_ref']}{(' (' + when + ')') if when else ''} — “{ev['quote']}”"
                )
    open_tasks = [t for t in detail["tasks"] if t["status"] in _OPEN]
    open_blockers = [b for b in detail["blockers"] if b["status"] in ("red", "yellow")]
    sections = [
        "TITLE",
        detail["title"],
        "",
        "SUMMARY",
        detail["summary"] or "No summary yet.",
        "",
        "STATUS",
        f"{detail['status'].upper()} — {detail['status_reason']}"
        f" (confidence {detail['confidence']:.2f};"
        f" priority {detail['priority_score']:.1f}: {detail['priority_reason']})",
        "",
        "KEY DECISIONS",
        *_lines(detail["decisions"], "none recorded"),
        "",
        "OPEN TASKS",
        *_lines(open_tasks, "none open"),
        "",
        "BLOCKERS",
        *_lines(open_blockers, "none open"),
        "",
        "NEXT ACTIONS",
        *(
            [f"{n}. {a['action']}" for n, a in enumerate(detail["next_actions"], 1)]
            or ["- nothing pending"]
        ),
        "",
        "SOURCE REFERENCES",
        *(refs or ["- no quoted sources"]),
    ]
    return "\n".join(sections) + "\n"


__all__ = [
    "STATUS_COLOURS",
    "entity_detail",
    "find_source",
    "graph",
    "health_rules",
    "items",
    "list_projects",
    "make_report",
    "next_actions",
    "project_detail",
    "resolve_project",
    "source_jump",
]
