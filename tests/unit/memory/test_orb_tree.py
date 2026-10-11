"""The memory orb's tree: work communities from the brain map, note communities from the vault."""

from __future__ import annotations

import random
import time

from jarvis.memory import orb_tree
from jarvis.memory.orb_tree import (
    MIN_COMMUNITY,
    SPLIT_ABOVE,
    TEXT_CHARS,
    NoteRow,
    build_tree,
    link_communities,
    note_groups,
    text_topics,
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


# --- Topics from text, for link-poor areas (an imported chat export) ---------

_IMPORT = ("imports", "ai-data-test")
_TOPIC_WORDS = {
    "docker": "docker compose nginx container volume port image yaml network proxy",
    "pasta": "pasta carbonara pecorino guanciale spaghetti sauce boil salt recipe dough",
    "lisbon": "lisbon tram alfama hotel flight itinerary museum pastel belem ferry",
}
_FILLER = (
    "question problem today maybe example work idea thing step point time help "
    "explain better answer start right check simple quick follow detail option"
).split()


def _chat(slug: str, title: str, words: list[str]) -> NoteRow:
    body = (
        f"# {title}\n\n"
        f'<!-- jarvis-conversation id="{slug}" format="chatgpt" created_ms="1700000000000" -->\n'
        "_Conversation imported from `conversations.json`, started 2023-11-14 22:13 UTC_\n\n"
        f'<!-- jarvis-turn id="{slug}-1" -->\n**You:**\n\n' + " ".join(words) + "\n"
    )
    return NoteRow(
        slug=slug, title=title, kind="meta", folder=_IMPORT, links=(), text=body[:TEXT_CHARS]
    )


def _three_topics(seed: int = 1) -> tuple[list[NoteRow], dict[str, str | None]]:
    """Eight chats on each of three subjects plus sixteen one-off chats."""
    rng = random.Random(seed)  # noqa: S311 — test determinism, not crypto
    notes: list[NoteRow] = []
    truth: dict[str, str | None] = {}
    for topic, pool in _TOPIC_WORDS.items():
        vocab = pool.split()
        for i in range(8):
            words = rng.choices(vocab, k=25) + rng.choices(_FILLER, k=40)
            slug = f"{topic}-chat-{i}"
            notes.append(_chat(slug, f"{rng.choice(vocab).title()} {rng.choice(_FILLER)}", words))
            truth[slug] = topic
    for i in range(16):
        # A subject of its own: words no other chat uses, plus filler.
        own = [f"{w}{i}" for w in ("glimmer", "quartz", "violin", "harbor", "saddle")]
        words = rng.choices(own, k=10) + rng.choices(_FILLER, k=40)
        slug = f"oneoff-{i:02d}"
        notes.append(_chat(slug, f"Note {own[0]}", words))
        truth[slug] = None
    return notes, truth


def _snapshot(notes: list[NoteRow]) -> list[tuple[str, str, tuple[str, ...]]]:
    return [(g.id, g.title, tuple(n.slug for n in g.notes)) for g in note_groups(notes)]


def test_link_poor_chats_group_into_named_topics() -> None:
    notes, truth = _three_topics()
    assert len(notes) > SPLIT_ABOVE
    groups = note_groups(notes)
    topics = [g for g in groups if g.by_text]
    assert len(topics) == 3
    for group in topics:
        subjects = {truth[n.slug] for n in group.notes}
        assert len(subjects) == 1 and None not in subjects, group
        (subject,) = subjects
        assert len(group.notes) == 8
        words = group.title.split(" · ")
        assert 2 <= len(words) <= 3
        assert set(words) <= set(_TOPIC_WORDS[subject].split()), group.title
    (loose,) = [g for g in groups if g.loose]
    assert loose.title == "Loose notes"
    assert {truth[n.slug] for n in loose.notes} == {None}
    # The orb draws exactly these groups.
    tree = build_tree(notes=notes, entities=[], edges=[])
    subs = {c["id"]: c for c in tree["communities"] if c["level"] == 2}
    assert {g.id for g in groups} == set(subs)
    assert {subs[g.id]["title"] for g in topics} == {g.title for g in topics}


def test_topics_are_deterministic_and_cached() -> None:
    notes, _ = _three_topics()
    first = _snapshot(notes)
    orb_tree._TOPIC_CACHE.clear()
    shuffled = list(notes)
    random.Random(9).shuffle(shuffled)  # noqa: S311 — test determinism
    assert _snapshot(shuffled) == first
    loose = sorted(notes, key=lambda n: n.slug)
    assert text_topics(loose) is text_topics(list(loose)), "an unchanged area is not recomputed"


def test_isolated_notes_stay_loose() -> None:
    rng = random.Random(3)  # noqa: S311 — test determinism, not crypto
    notes = [
        _chat(
            f"alone-{i:02d}",
            f"Alone {i}",
            [f"word{i}x{j}" for j in range(12)] + rng.choices(_FILLER, k=30),
        )
        for i in range(30)
    ]
    groups = note_groups(notes)
    assert [(g.title, len(g.notes)) for g in groups] == [("Loose notes", 30)]


def test_linked_areas_keep_their_link_communities() -> None:
    # Most notes linked: text topics are not consulted, hubs still name groups.
    left = [f"left-{i}" for i in range(15)]
    notes = [_note(s, folder=_IMPORT, links=tuple(x for x in left if x != s)) for s in left]
    notes += _three_topics()[0][:12]
    groups = note_groups(notes)
    assert not any(g.by_text for g in groups)


def test_a_2400_chat_import_groups_within_seconds() -> None:
    rng = random.Random(7)  # noqa: S311 — test determinism, not crypto
    vocab = [f"w{i}" for i in range(3000)]
    # Zipf-like everyday words shared by every chat, as in real exports.
    cum: list[float] = []
    for i in range(len(vocab)):
        cum.append((cum[-1] if cum else 0.0) + 1 / (i + 1))
    topics = [[f"t{t}k{k}" for k in range(25)] for t in range(40)]
    notes = []
    for i in range(2400):
        topic = topics[i % 40] if i % 5 else None
        words = rng.choices(vocab, cum_weights=cum, k=160)
        if topic is not None:
            words += rng.choices(topic, k=40)
        notes.append(_chat(f"chat-{i:05d}", f"Chat {i}", words))
    orb_tree._TOPIC_CACHE.clear()
    started = time.perf_counter()
    groups = note_groups(notes)
    elapsed = time.perf_counter() - started
    # Measured about 1.5 s in pure Python; the bound leaves room for slow CI.
    assert elapsed < 8.0, f"grouping took {elapsed:.2f}s"
    assert sum(1 for g in groups if g.by_text) >= 30
