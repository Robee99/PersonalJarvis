"""Read-only brain map tools for Jarvis's own MCP server.

Six tools an agent session (or Hermes) can call to ask about projects:
``project_summary``, ``open_tasks``, ``blockers``, ``find_source``,
``make_report``, ``next_action``. Each is a pure read over the derived
tables — no write, no model call, no source text beyond the stored quotes —
so they are offered with ``readOnlyHint`` and answered in-process by
``jarvis/mcp/jarvis_tools_server.py`` next to the gateway's catalog.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import query
from .service import BrainMapService, shared_service

_PROJECT = {
    "type": "string",
    "description": "Project id (project:...) or a project name; omit for all projects.",
}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}


@dataclass(frozen=True, slots=True)
class BrainMapTool:
    name: str
    description: str
    input_schema: dict[str, Any]


TOOLS: tuple[BrainMapTool, ...] = (
    BrainMapTool(
        "project_summary",
        "Brain map: status (red/blue/yellow/green with the reason), priority and summary of one "
        "project, or of every project when none is named. Read-only.",
        {"type": "object", "properties": {"project": _PROJECT}},
    ),
    BrainMapTool(
        "open_tasks",
        "Brain map: open tasks, highest priority first, for one project or all. Read-only.",
        {"type": "object", "properties": {"project": _PROJECT, "limit": _LIMIT}},
    ),
    BrainMapTool(
        "blockers",
        "Brain map: open blockers (what is blocked on what, with the quote), for one project or "
        "all. Read-only.",
        {"type": "object", "properties": {"project": _PROJECT, "limit": _LIMIT}},
    ),
    BrainMapTool(
        "find_source",
        "Brain map: find where something was said or decided — matching entities with their "
        "verbatim quote and the source (conversation id, message, timestamp). Read-only.",
        {
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 2}, "limit": _LIMIT},
            "required": ["query"],
        },
    ),
    BrainMapTool(
        "make_report",
        "Brain map: a project report with TITLE, SUMMARY, STATUS, KEY DECISIONS, OPEN TASKS, "
        "BLOCKERS, NEXT ACTIONS and SOURCE REFERENCES. Read-only.",
        {
            "type": "object",
            "properties": {"project": {**_PROJECT, "description": "Project id or name."}},
            "required": ["project"],
        },
    ),
    BrainMapTool(
        "next_action",
        "Brain map: the single most important next action (and why), for one project or across "
        "all projects. Read-only.",
        {"type": "object", "properties": {"project": _PROJECT}},
    ),
)

TOOL_NAMES: frozenset[str] = frozenset(t.name for t in TOOLS)


def _line(item: dict[str, Any]) -> str:
    return (
        f"- [{item['status'].upper()}] {item['title']} ({item['kind'].lower()}, id {item['id']}): "
        f"{item['status_reason']}. Priority {item['priority_score']:.1f}: {item['priority_reason']}"
    )


def _limit(args: dict[str, Any]) -> int:
    try:
        return max(1, min(50, int(args.get("limit") or 10)))
    except (TypeError, ValueError):  # a non-numeric limit falls back to the default
        return 10


def answer(name: str, args: dict[str, Any], service: BrainMapService) -> tuple[str, bool]:
    """``(text, is_error)`` for one tool call. Synchronous: call it off the loop."""
    store = service.store
    ref = str(args.get("project") or "").strip()
    project_id = query.resolve_project(store, ref) if ref else None
    if ref and project_id is None:
        return f"No project matches {ref!r}.", True
    if name == "project_summary":
        if project_id:
            detail = query.project_detail(store, project_id)
            if detail is None:
                return "No such project.", True
            return json.dumps(
                {
                    k: detail[k]
                    for k in (
                        "id",
                        "title",
                        "status",
                        "status_reason",
                        "health",
                        "confidence",
                        "priority",
                        "summary",
                        "counts",
                        "next_actions",
                    )
                },
                ensure_ascii=False,
            ), False
        projects = query.list_projects(store)
        if not projects:
            return "The brain map has no projects yet.", False
        return "\n".join(_line(p) + f" — {p['summary']}" for p in projects[:30]), False
    if name in ("open_tasks", "blockers"):
        kind = "TASK" if name == "open_tasks" else "BLOCKER"
        found = query.items(store, kind, project_id=project_id)[: _limit(args)]
        if not found:
            return f"No open {'tasks' if kind == 'TASK' else 'blockers'}.", False
        return "\n".join(_line(i) for i in found), False
    if name == "find_source":
        text = str(args.get("query") or "").strip()
        if len(text) < 2:
            return "Give a query of at least two characters.", True
        hits = query.find_source(store, text, limit=_limit(args))
        if not hits:
            return "Nothing in the brain map matches.", False
        out = []
        for hit in hits:
            out.append(_line(hit))
            for ev in hit["evidence"][:3]:
                src = ev["source"]
                out.append(
                    f"    “{ev['quote']}” — {src['source_kind']}"
                    f" {src['conversation_id'] or src['source_id']}"
                    f" {src['message_ref']} at {src['ts_ms']}"
                )
        return "\n".join(out), False
    if name == "make_report":
        if not project_id:
            return "Name the project to report on.", True
        report = query.make_report(store, project_id)
        return (report, False) if report else ("No such project.", True)
    if name == "next_action":
        ids = [project_id] if project_id else [p["id"] for p in query.list_projects(store)]
        best: tuple[float, str, dict[str, Any]] | None = None
        for pid in ids:
            detail = query.project_detail(store, pid)
            if detail is None or not detail["next_actions"]:
                continue
            action = detail["next_actions"][0]
            score = float(detail["priority_score"])
            if best is None or score > best[0]:
                best = (score, detail["title"], action)
        if best is None:
            return "Nothing needs doing right now.", False
        return f"{best[2]['action']} (project {best[1]}). Why: {best[2]['why']}", False
    return f"Unknown brain map tool {name!r}.", True


async def call(
    name: str,
    args: dict[str, Any] | None,
    service_factory: Callable[[], BrainMapService] = shared_service,
) -> tuple[str, bool]:
    return await asyncio.to_thread(answer, name, dict(args or {}), service_factory())


__all__ = ["TOOLS", "TOOL_NAMES", "BrainMapTool", "answer", "call"]
