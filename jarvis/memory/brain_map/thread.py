"""A project's related conversations as one branching thread.

The trunk is the project itself; its branches are what hangs off it directly
(conversations and IDE threads as topics, missions as experiments, decisions,
tasks, blockers), in the order they first appeared. A branch's own children
are what was said or produced inside it: a conversation's decisions and
subtasks, a mission's outputs. Nothing is invented: the shape is exactly the
``part_of`` / ``decided_in`` / ``blocks`` / ``produced`` walk that
``engine.members_of`` already does, read back as a tree via each member's
``via``.

Bounded so a large project stays readable: the newest ``MAX_BRANCHES`` trunk
branches and ``MAX_CHILDREN`` children per branch are kept, and what was cut
is counted (``hidden_branches`` / ``hidden_children``) so the Orb can say so.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final

#: Branch vocabulary; mirrored by ``ThreadBranchKind`` in brain_map_routes.py
#: and by ``BRANCH_KINDS`` in the Orb (src/components/orb/brainMap.ts).
BRANCH_KINDS: Final[tuple[str, ...]] = (
    "topic",
    "decision",
    "experiment",
    "subtask",
    "blocker",
    "milestone",
    "artifact",
)
MAX_BRANCHES: Final[int] = 120
MAX_CHILDREN: Final[int] = 30

#: Task sources that are agent runs rather than to-dos.
_EXPERIMENT_SOURCES: Final[frozenset[str]] = frozenset({"mission", "quest"})


def branch_kind(kind: str, source_kind: str) -> str | None:
    """What a member is on the thread; ``None`` keeps it off the thread."""
    if kind in ("CONVERSATION", "THREAD"):
        return "topic"
    if kind == "DECISION":
        return "decision"
    if kind == "TASK":
        return "experiment" if source_kind in _EXPERIMENT_SOURCES else "subtask"
    if kind == "BLOCKER":
        return "blocker"
    if kind == "MILESTONE":
        return "milestone"
    if kind in ("OUTPUT", "DOCUMENT"):
        return "artifact"
    return None


def _order(entry: Mapping[str, Any]) -> tuple[int, str]:
    when = int(entry.get("first_seen_ms") or 0) or int(entry.get("last_activity_ms") or 0)
    return when, str(entry["id"])


def build_thread(project_id: str, entries: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The tree for one project.

    ``entries`` are entity dicts (``query._entity``) with ``via`` (the entity
    each hangs off), ``first_seen_ms`` and ``source_kind``.
    """
    nodes: dict[str, dict[str, Any]] = {}
    parent_of: dict[str, str] = {}
    for entry in entries:
        kind = branch_kind(str(entry["kind"]), str(entry.get("source_kind") or ""))
        if kind is None:
            continue
        node = {
            key: entry[key]
            for key in (
                "id",
                "kind",
                "title",
                "status",
                "status_reason",
                "assignment",
                "last_activity_ms",
                "source",
            )
        }
        node.update(branch=kind, first_seen_ms=int(entry.get("first_seen_ms") or 0))
        nodes[str(entry["id"])] = node
        parent_of[str(entry["id"])] = str(entry.get("via") or project_id)

    children: dict[str, list[dict[str, Any]]] = {}
    for node_id, node in nodes.items():
        parent = parent_of[node_id]
        # A member whose parent is not on the thread (an agent, a cut branch)
        # joins the trunk rather than disappearing.
        if parent != project_id and parent not in nodes:
            parent = project_id
        children.setdefault(parent, []).append(node)

    def attach(node: dict[str, Any], depth: int) -> dict[str, Any]:
        kids = sorted(children.get(str(node["id"]), []), key=_order)
        shown = kids[-MAX_CHILDREN:]
        node["children"] = [attach(k, depth + 1) for k in shown] if depth < 3 else []
        node["hidden_children"] = len(kids) - len(node["children"])
        return node

    trunk = sorted(children.get(project_id, []), key=_order)
    shown = trunk[-MAX_BRANCHES:]
    counts = {k: 0 for k in BRANCH_KINDS}
    for node in nodes.values():
        counts[node["branch"]] += 1
    return {
        "project_id": project_id,
        "branches": [attach(n, 1) for n in shown],
        "hidden_branches": len(trunk) - len(shown),
        "counts": counts,
    }


__all__ = ["BRANCH_KINDS", "MAX_BRANCHES", "MAX_CHILDREN", "branch_kind", "build_thread"]
