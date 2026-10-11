"""The memory orb's tree: work communities from the brain map, note communities from the vault."""

from __future__ import annotations

from jarvis.memory.orb_tree import (
    MIN_COMMUNITY,
    SPLIT_ABOVE,
    NoteRow,
    build_tree,
    link_communities,
)


def _note(
    slug: str, folder: tuple[str, ...] = ("entities",), links: tuple[str, ...] = ()
) -> NoteRow:
    return NoteRow(
        slug=slug, title=slug.replace("-", " ").title(), kind="entity", folder=folder, links=links
    )


def test_projects_are_communities_coloured_by_health_with_their_items() -> None:
    entities = [
        {
            "id": "p1",
            "kind": "PROJECT",
            "title": "Voice OS",
            "status": "blue",
            "health": {"colour": "red", "reason": "2 open blockers", "progress": 0.1},
        },
        {"id": "t1", "kind": "TASK", "title": "Fix mic", "status": "red"},
        {"id": "t2", "kind": "TASK", "title": "Ship HUD", "status": "green"},
    ]
    edges = [
        {"source": "t1", "target": "p1", "kind": "part_of"},
        {"source": "t2", "target": "p1", "kind": "part_of"},
    ]
    tree = build_tree(notes=[], entities=entities, edges=edges)
    (project,) = tree["communities"]
    assert project["colour"] == "red"
    assert project["reason"] == "2 open blockers"
    assert project["ref"] == {"type": "project", "id": "p1"}
    leaves = {leaf["title"]: leaf for leaf in tree["leaves"]}
    assert leaves["Fix mic"]["colour"] == "red"
    assert leaves["Ship HUD"]["colour"] == "green"
    assert {leaf["community"] for leaf in tree["leaves"]} == {"project:p1"}


def test_each_import_is_its_own_area_and_untracked_notes_are_reference() -> None:
    notes = [
        _note("a", folder=("imports", "chatgpt-1")),
        _note("b", folder=("imports", "claude-2")),
        _note("c", folder=()),
    ]
    tree = build_tree(notes=notes, entities=[], edges=[])
    titles = {c["title"] for c in tree["communities"]}
    assert {"Import · chatgpt-1", "Import · claude-2"} <= titles
    assert {leaf["colour"] for leaf in tree["leaves"]} == {"reference"}


def test_a_note_named_by_tracked_work_takes_its_status() -> None:
    notes = [_note("hud-design")]
    entities = [{"id": "d1", "kind": "DOCUMENT", "title": "HUD design", "status": "yellow"}]
    tree = build_tree(notes=notes, entities=entities, edges=[])
    note = next(leaf for leaf in tree["leaves"] if leaf["id"] == "note:hud-design")
    assert note["colour"] == "yellow"


def test_a_large_area_splits_into_link_communities_named_by_their_hub() -> None:
    # Two cliques of linked notes plus loose singletons, in one big area.
    left = [f"left-{i}" for i in range(15)]
    right = [f"right-{i}" for i in range(15)]
    notes = [_note(s, links=tuple(x for x in left if x != s)) for s in left]
    notes += [_note(s, links=tuple(x for x in right if x != s)) for s in right]
    notes += [_note("lonely-1"), _note("lonely-2")]
    assert len(notes) > SPLIT_ABOVE
    tree = build_tree(notes=notes, entities=[], edges=[])
    subs = [c for c in tree["communities"] if c["level"] == 2]
    assert len([c for c in subs if c["title"] != "Loose notes"]) == 2
    assert any(c["title"] == "Loose notes" and c["size"] < MIN_COMMUNITY for c in subs)
    area = next(c for c in tree["communities"] if c["level"] == 1)
    assert all(c["parent"] == area["id"] for c in subs)
    # Every note sits in exactly one community.
    assert len(tree["leaves"]) == len(notes)


def test_link_communities_are_deterministic() -> None:
    slugs = ["a", "b", "c", "d"]
    links = {"a": ["b"], "b": ["a"], "c": ["d"]}
    first = link_communities(slugs, links)
    assert first == link_communities(list(reversed(slugs)), links)
    assert first["a"] == first["b"] != first["c"] == first["d"]
