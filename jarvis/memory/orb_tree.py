"""The memory orb's tree: everything remembered, grouped the way GraphRAG groups it.

Microsoft's GraphRAG answers "what is in here?" with a hierarchy: entities at
the bottom, communities of related entities above them, and a few top-level
communities over those. This module draws Jarvis's memory in that shape,
from the two stores that already exist, and invents nothing:

* **Work** comes from the project brain map (``jarvis/memory/brain_map``).
  Each project is a top-level community, its conversations, tasks, decisions
  and blockers are its members (the ``part_of`` relations), and every colour
  is the brain map's own: a project's measured health, an item's status.
* **Notes** come from the wiki vault. A top-level community is a vault area
  (``entities``, ``concepts``, one per imported dataset under ``imports/``).
  Inside an area too large to read as one list, notes are grouped into
  communities of their own by their wikilinks (label propagation, the same
  idea as GraphRAG's Leiden step, deterministic so the orb does not reshuffle
  between visits). A community is named after its most linked note.

A note is coloured by work only when the brain map tracks it (an item whose
title or source names the note); every other note is "reference", and the
legend says so. Pure: the route reads the stores and hands rows in.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: Notes per area above which the area is split into link communities.
SPLIT_ABOVE = 24
#: Label-propagation rounds; converges in a handful on vault-sized graphs.
_ROUNDS = 12
#: Smallest link community kept on its own; smaller ones join "Loose notes".
MIN_COMMUNITY = 3
#: Most leaves sent per community, so a 5,000-note vault stays drawable.
MAX_LEAVES_PER_COMMUNITY = 160

REFERENCE = "reference"
STATUSES = ("red", "yellow", "blue", "green")


@dataclass(frozen=True, slots=True)
class NoteRow:
    slug: str
    title: str
    kind: str
    #: Vault-relative folder parts, e.g. ``("imports", "ai-data-d81b09")``.
    folder: tuple[str, ...]
    links: tuple[str, ...]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def area_of(note: NoteRow) -> tuple[str, str]:
    """``(area id, area title)`` for a note: its top folder, or its import."""
    parts = [p for p in note.folder if p]
    if not parts:
        return ("area:" + (note.kind or "notes"), (note.kind or "notes").title())
    if parts[0].lower() == "imports" and len(parts) > 1:
        return ("area:imports/" + parts[1], "Import · " + parts[1])
    return ("area:" + parts[0].lower(), parts[0].replace("-", " ").replace("_", " ").title())


def link_communities(slugs: Sequence[str], links: Mapping[str, Iterable[str]]) -> dict[str, str]:
    """Each slug's community label by label propagation over undirected links.

    Deterministic: nodes are visited in sorted order and ties go to the
    smallest label, so the same vault always yields the same communities.
    """
    members = set(slugs)
    neighbours: dict[str, set[str]] = {s: set() for s in members}
    for src, targets in links.items():
        if src not in members:
            continue
        for dst in targets:
            if dst in members and dst != src:
                neighbours[src].add(dst)
                neighbours[dst].add(src)
    label = {s: s for s in members}
    order = sorted(members)
    for _ in range(_ROUNDS):
        changed = False
        for node in order:
            if not neighbours[node]:
                continue
            counts = Counter(label[n] for n in neighbours[node])
            best = max(counts.values())
            choice = min(lab for lab, c in counts.items() if c == best)
            if choice != label[node]:
                label[node] = choice
                changed = True
        if not changed:
            break
    return label


def _status_index(entities: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Normalised note name → status, for brain-map items that name a note."""
    index: dict[str, str] = {}
    for entity in entities:
        if entity.get("kind") == "PROJECT":
            continue
        status = str(entity.get("status") or "")
        if status not in STATUSES:
            continue
        source = entity.get("source") or {}
        for name in (entity.get("title"), source.get("locator"), source.get("source_id")):
            key = _norm(str(name or "").rsplit("/", 1)[-1].removesuffix(".md"))
            if len(key) >= 4:
                index.setdefault(key, status)
    return index


def _majority(colours: Iterable[str]) -> str:
    counted = Counter(c for c in colours if c in STATUSES)
    if not counted:
        return REFERENCE
    # Worst colour among the most common ones: what needs a look comes first.
    best = max(counted.values())
    return next(c for c in STATUSES if counted.get(c) == best)


def build_tree(
    *,
    notes: Sequence[NoteRow],
    entities: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The orb's communities and leaves. See the module docstring."""
    communities: list[dict[str, Any]] = []
    leaves: list[dict[str, Any]] = []

    # --- Work: projects and their members ---------------------------------
    by_id = {str(e.get("id")): e for e in entities}
    members: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.get("kind") == "part_of" and str(edge.get("target")) in by_id:
            members[str(edge["target"])].append(str(edge.get("source")))
    for entity in entities:
        if entity.get("kind") != "PROJECT":
            continue
        pid = str(entity["id"])
        health = entity.get("health") or {}
        colour = str(health.get("colour") or entity.get("status") or "blue")
        cid = "project:" + pid
        kids = [by_id[m] for m in dict.fromkeys(members.get(pid, [])) if m in by_id]
        communities.append(
            {
                "id": cid,
                "title": str(entity.get("title") or pid),
                "level": 1,
                "parent": None,
                "kind": "project",
                "colour": colour,
                "reason": str(health.get("reason") or entity.get("status_reason") or ""),
                "progress": health.get("progress"),
                "ref": {"type": "project", "id": pid},
                "size": len(kids),
            }
        )
        for kid in kids[:MAX_LEAVES_PER_COMMUNITY]:
            leaves.append(
                {
                    "id": "entity:" + str(kid["id"]),
                    "title": str(kid.get("title") or kid["id"]),
                    "kind": str(kid.get("kind") or "ITEM").lower(),
                    "colour": str(kid.get("status") or "blue"),
                    "community": cid,
                    "ref": {"type": "entity", "id": str(kid["id"])},
                }
            )

    # --- Notes: vault areas, split into link communities --------------------
    tracked = _status_index(entities)
    by_area: dict[str, list[NoteRow]] = defaultdict(list)
    area_titles: dict[str, str] = {}
    for note in notes:
        aid, title = area_of(note)
        by_area[aid].append(note)
        area_titles[aid] = title
    links = {n.slug: n.links for n in notes}
    degree: Counter[str] = Counter()
    for note in notes:
        for dst in note.links:
            degree[note.slug] += 1
            degree[dst] += 1

    def colour_of(note: NoteRow) -> str:
        return tracked.get(_norm(note.slug)) or tracked.get(_norm(note.title)) or REFERENCE

    for aid in sorted(by_area, key=lambda a: (-len(by_area[a]), a)):
        group = sorted(by_area[aid], key=lambda n: n.slug)
        groups: dict[str, list[NoteRow]]
        if len(group) > SPLIT_ABOVE:
            label = link_communities([n.slug for n in group], links)
            raw: dict[str, list[NoteRow]] = defaultdict(list)
            for note in group:
                raw[label[note.slug]].append(note)
            groups = {}
            loose: list[NoteRow] = []
            for lab, rows in raw.items():
                if len(rows) >= MIN_COMMUNITY:
                    groups[lab] = rows
                else:
                    loose.extend(rows)
            if loose:
                groups["~loose"] = loose
        else:
            groups = {"~all": group}
        area_colours: list[str] = []
        area_cid = aid
        sub: list[dict[str, Any]] = []
        for lab, rows in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            colours = [colour_of(n) for n in rows]
            area_colours.extend(colours)
            if lab == "~all":
                cid, parent = area_cid, None
            else:
                head = max(rows, key=lambda n: (degree[n.slug], n.title))
                title = "Loose notes" if lab == "~loose" else head.title
                cid, parent = f"{area_cid}#{lab}", area_cid
                sub.append(
                    {
                        "id": cid,
                        "title": title,
                        "level": 2,
                        "parent": parent,
                        "kind": "notes",
                        "colour": _majority(colours),
                        "reason": "",
                        "progress": None,
                        "ref": None,
                        "size": len(rows),
                    }
                )
            ranked = sorted(rows, key=lambda n: (-degree[n.slug], n.title))
            for note in ranked[:MAX_LEAVES_PER_COMMUNITY]:
                leaves.append(
                    {
                        "id": "note:" + note.slug,
                        "title": note.title,
                        "kind": note.kind or "note",
                        "colour": colour_of(note),
                        "community": cid,
                        "ref": {"type": "note", "slug": note.slug},
                    }
                )
        communities.append(
            {
                "id": area_cid,
                "title": area_titles[aid],
                "level": 1,
                "parent": None,
                "kind": "area",
                "colour": _majority(area_colours),
                "reason": "",
                "progress": None,
                "ref": None,
                "size": len(group),
            }
        )
        communities.extend(sub)

    counts = Counter(leaf["colour"] for leaf in leaves)
    return {
        "communities": communities,
        "leaves": leaves,
        "counts": {c: counts.get(c, 0) for c in (*STATUSES, REFERENCE)},
        "totals": {
            "notes": len(notes),
            "projects": sum(1 for c in communities if c["kind"] == "project"),
        },
        "legend": {
            "red": "blocked, failed or barely started",
            "yellow": "partly done",
            "blue": "active or under review",
            "green": "done and verified",
            REFERENCE: "a note no tracked work points at",
        },
    }
