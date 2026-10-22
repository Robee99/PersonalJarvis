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
* **Topics** come from what notes say when their links say nothing. An
  imported chat export is thousands of pages with no wikilinks, so links
  alone leave the whole import in "Loose notes". When most of a split area
  is loose, each loose note is linked to the few notes whose words are most
  alike (TF-IDF cosine over the title and the start of the body) and the
  same label propagation groups those links. A topic is named after the
  words its notes share and the rest of the area does not ("docker · compose
  · nginx"). Notes alike to nothing stay loose.

A note is coloured by work only when the brain map tracks it (an item whose
title or source names the note); every other note is "reference", and the
legend says so. Pure: the route reads the stores and hands rows in. The
``memory_chats`` tool reads the same groups through :func:`note_groups`, so
what Jarvis's tools call a topic is what the orb draws.
"""

from __future__ import annotations

import hashlib
import heapq
import math
import re
import threading
from collections import Counter, OrderedDict, defaultdict
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

#: Characters of a note's body the topic finder reads, after its title. A
#: chat's opening states what it is about; the rest mostly elaborates.
TEXT_CHARS = 1500
#: Nearest notes each loose note is linked to by text.
TEXT_NEIGHBOURS = 3
#: Smallest TF-IDF cosine that links two notes by text. Measured on a
#: synthetic 2,400-chat area (40 topics, a fifth noise): a chat's best match
#: on another subject stays under 0.14 for 9 in 10 chats, its best match on
#: its own subject is 0.18 or more for 9 in 10 even when only 8% of its words
#: are topical. 0.18 sits in that gap: lower and generic chats chain topics
#: together, higher and short chats on one subject stop finding each other.
TEXT_LINK_MIN = 0.18
#: A term in more than this share of an area's notes names no topic.
_MAX_DF_SHARE = 0.25
#: A term in fewer notes than this cannot link two of them.
_MIN_DF = 2
#: Terms kept per note vector (its most weighted ones). Specific words have
#: short posting lists, so this bounds the inverted-index work.
_TERMS_PER_NOTE = 24
#: A title word counts this many times: titles say what a chat is about.
_TITLE_WEIGHT = 2
#: Terms kept per topic centroid when fragments are compared.
_CENTROID_TERMS = 40
#: Centroid cosine at which two groups found by text are one topic. In the
#: synthetic 2,400-chat area, pieces of one subject measured 0.30 against each
#: other at the median and different subjects 0.09 at most; 0.3 joins only
#: clear cases.
_TOPIC_MERGE_MIN = 0.3
#: Area topic results kept, keyed by the notes they were computed from.
_TOPIC_CACHE_SIZE = 8

_Vector = list[tuple[str, float]]

_WORD = re.compile(r"[^\W\d_][^\W_]{2,29}")
#: Markup that is not content: HTML comments (the importer's provenance
#: markers), links and code-fence language tags.
_NOISE = re.compile(r"<!--.*?(?:-->|$)|https?://\S+|`{3}[a-z]*", re.DOTALL)
#: Words with no topic in them, in the languages the owner writes in. Terms in
#: more than a quarter of the notes are dropped anyway; this list only keeps
#: small areas from naming a topic "what · with · this".
STOPWORDS: frozenset[str] = frozenset(
    (
        "the and for with that this from have has had was were are you your yours "
        "not but can could would should will what when where which who how why "
        "all any some just also into about there their them they then than out "
        "our ours its does did doing done get got make made "
        "use using used like want need one two more most very much here only "
        "over such each other these those been being let please thanks thank yes "
        "user assistant conversation imported started utc "
        # German
        "und der die das den dem des ein eine einen einem einer nicht ist sind "  # i18n-allow
        "mit von auf aus bei nach wie was wer wir ihr sie ich mir mich dir dich "  # i18n-allow
        "auch noch nur aber oder wenn dann doch schon kann muss soll wird werden "  # i18n-allow
        "hat haben habe sein bist war zum zur fuer ueber dass diese dieser dies "  # i18n-allow
        "für über "  # i18n-allow
        # Spanish
        "que los las del por para con una uno unos unas como pero más mas "
        "esta este esto estos sus ser son está hay muy sin sobre también "
        "tambien cuando donde porque puedes puede tengo tiene hola gracias"
    ).split()
)

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
    #: The first :data:`TEXT_CHARS` characters of the body, for topics.
    text: str = ""


@dataclass(frozen=True, slots=True)
class NoteGroup:
    """One note community as the orb draws it (see :func:`note_groups`)."""

    #: The orb's community id: the area id, or ``<area>#<label>`` when split.
    id: str
    title: str
    area: str
    area_title: str
    notes: tuple[NoteRow, ...]
    #: True when the area is too small to split and the group is all of it.
    whole_area: bool = False
    #: True for a topic found from the notes' words, not their links.
    by_text: bool = False

    @property
    def loose(self) -> bool:
        return self.id.endswith("#~loose")


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


def link_communities(
    slugs: Sequence[str],
    links: Mapping[str, Iterable[str]],
    weights: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, str]:
    """Each slug's community label by label propagation over undirected links.

    With ``weights`` (``weights[a][b]``, symmetric) a neighbour's label counts
    by how strongly it is linked instead of once, so a note follows the notes
    it is most alike rather than whichever label is smallest.

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
    # Weighted sums are added in a fixed order so float rounding cannot
    # differ between runs.
    ordered = {s: sorted(neighbours[s]) for s in order} if weights is not None else {}
    for _ in range(_ROUNDS):
        changed = False
        for node in order:
            if not neighbours[node]:
                continue
            counts: Mapping[str, float]
            if weights is None:
                counts = Counter(label[n] for n in neighbours[node])
            else:
                summed: dict[str, float] = defaultdict(float)
                mine = weights.get(node, {})
                for n in ordered[node]:
                    summed[label[n]] += mine.get(n, 1.0)
                counts = summed
            best = max(counts.values())
            choice = min(lab for lab, c in counts.items() if c == best)
            if choice != label[node]:
                label[node] = choice
                changed = True
        if not changed:
            break
    return label


def terms_of(text: str) -> list[str]:
    """The topic words of ``text``: lowercased, 3 to 30 letters, no stopwords."""
    cleaned = _NOISE.sub(" ", text.lower())
    return [w for w in _WORD.findall(cleaned) if w not in STOPWORDS]


def _bag(note: NoteRow) -> Counter[str]:
    bag = Counter(terms_of(note.text[:TEXT_CHARS]))
    for term in terms_of(note.title):
        bag[term] += _TITLE_WEIGHT
    return bag


def _idf(bags: Sequence[Counter[str]]) -> dict[str, float]:
    """Inverse document frequency of every term that can tell notes apart."""
    total = len(bags)
    df = Counter(term for bag in bags for term in bag)
    most = _MAX_DF_SHARE * total
    return {t: math.log(total / c) for t, c in df.items() if _MIN_DF <= c <= most}


def text_links(notes: Sequence[NoteRow]) -> dict[str, dict[str, float]]:
    """Each note's nearest notes by what they say: ``{slug: {slug: cosine}}``.

    TF-IDF vectors over the title and the start of the body, each cut to its
    :data:`_TERMS_PER_NOTE` heaviest terms, compared through an inverted index
    so only notes sharing a term are ever scored. Every note keeps its
    :data:`TEXT_NEIGHBOURS` best matches at or above :data:`TEXT_LINK_MIN`;
    the result is symmetric. Deterministic: terms and postings are visited in
    a fixed order, so the sums round the same way every time.
    """
    bags = [_bag(n) for n in notes]
    return _nearest([n.slug for n in notes], _vectors(bags, _idf(bags)))


def _vectors(bags: Sequence[Counter[str]], idf: Mapping[str, float]) -> list[_Vector]:
    """Unit TF-IDF vectors, each cut to its :data:`_TERMS_PER_NOTE` heaviest terms."""
    vectors: list[_Vector] = []
    for bag in bags:
        weighted = [((1.0 + math.log(c)) * idf[t], t) for t, c in bag.items() if t in idf]
        top = sorted(weighted, key=lambda wt: (-wt[0], wt[1]))[:_TERMS_PER_NOTE]
        norm = math.sqrt(sum(w * w for w, _ in top)) or 1.0
        vectors.append([(t, w / norm) for w, t in top])
    return vectors


def _nearest(slugs: Sequence[str], vectors: Sequence[_Vector]) -> dict[str, dict[str, float]]:
    postings: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for index, vector in enumerate(vectors):
        for term, weight in vector:
            postings[term].append((index, weight))
    out: dict[str, dict[str, float]] = {s: {} for s in slugs}
    for index, vector in enumerate(vectors):
        scores: dict[int, float] = defaultdict(float)
        for term, weight in vector:
            for other, other_weight in postings[term]:
                scores[other] += weight * other_weight
        scores.pop(index, None)
        best = heapq.nsmallest(
            TEXT_NEIGHBOURS,
            ((-score, slugs[other]) for other, score in scores.items() if score >= TEXT_LINK_MIN),
        )
        for negative, other_slug in best:
            out[slugs[index]][other_slug] = -negative
            out[other_slug][slugs[index]] = -negative
    return out


def _centroid(vectors: Iterable[_Vector]) -> dict[str, float]:
    """The unit mean of some note vectors, cut to its heaviest terms."""
    summed: dict[str, float] = defaultdict(float)
    for vector in vectors:
        for term, weight in vector:
            summed[term] += weight
    top = sorted(summed.items(), key=lambda tw: (-tw[1], tw[0]))[:_CENTROID_TERMS]
    norm = math.sqrt(sum(w * w for _, w in top)) or 1.0
    return {t: w / norm for t, w in top}


def _merge_fragments(
    slugs: Sequence[str],
    label: Mapping[str, str],
    vectors: Sequence[_Vector],
    similar: Mapping[str, Mapping[str, float]],
) -> dict[str, str]:
    """Join pieces of one topic that label propagation left apart.

    On a nearest-neighbour graph, label propagation settles into several
    plateaus per large topic. Two groups that a text link crosses and whose
    centroids are at least :data:`_TOPIC_MERGE_MIN` alike become one, most
    alike pair first; the merged label is the smaller one. Deterministic.
    """
    members: dict[str, list[int]] = defaultdict(list)
    for index, slug in enumerate(slugs):
        members[label[slug]].append(index)
    centroids = {
        lab: _centroid(vectors[i] for i in idx) for lab, idx in members.items() if len(idx) > 1
    }
    pairs: set[tuple[str, str]] = set()
    for slug, others in similar.items():
        for other in others:
            a, b = sorted((label[slug], label[other]))
            if a != b and a in centroids and b in centroids:
                pairs.add((a, b))
    scored = []
    for a, b in pairs:
        small, large = sorted((centroids[a], centroids[b]), key=len)
        cosine = sum(w * large.get(t, 0.0) for t, w in small.items())
        if cosine >= _TOPIC_MERGE_MIN:
            scored.append((-cosine, a, b))
    parent: dict[str, str] = {}

    def root(lab: str) -> str:
        while parent.get(lab, lab) != lab:
            lab = parent[lab]
        return lab

    for _, a, b in sorted(scored):
        ra, rb = root(a), root(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    return {slug: root(label[slug]) for slug in slugs}


def _topic_name(bags: Sequence[Counter[str]], idf: Mapping[str, float]) -> str:
    """Two or three words a topic's notes share and the rest of the area does not.

    A word scores by the share of the topic's notes that use it times how rare
    it is in the area; it must appear in a quarter of the topic (two notes at
    least). A word that only extends one already chosen ("docker" and
    "dockerfile") is skipped. Empty when fewer than two words qualify.
    """
    size = len(bags)
    used = Counter(term for bag in bags for term in bag if term in idf)
    floor = max(2, math.ceil(size / 4))
    ranked = sorted(
        (-(c / size) * idf[t], t) for t, c in used.items() if c >= floor
    )
    chosen: list[str] = []
    for _, term in ranked:
        if any(term.startswith(c) or c.startswith(term) for c in chosen):
            continue
        chosen.append(term)
        if len(chosen) == 3:
            break
    return " · ".join(chosen) if len(chosen) >= 2 else ""


_TOPIC_CACHE: OrderedDict[bytes, tuple[dict[str, str], dict[str, str]]] = OrderedDict()
_TOPIC_CACHE_LOCK = threading.Lock()


def _topics_key(notes: Sequence[NoteRow]) -> bytes:
    digest = hashlib.blake2b(digest_size=20)
    for note in notes:
        for part in (note.slug, note.title, note.text):
            digest.update(part.encode("utf-8", "replace"))
            digest.update(b"\0")
    return digest.digest()


def text_topics(notes: Sequence[NoteRow]) -> tuple[dict[str, str], dict[str, str]]:
    """``(label of each slug, name of each topic label)`` for link-poor notes.

    Labels come from weighted :func:`link_communities` over the text links
    (:func:`text_links`), with pieces of one topic joined afterwards
    (:func:`_merge_fragments`). Only labels shared by :data:`MIN_COMMUNITY`
    notes or more are named topics; the rest stay loose. Cached on the notes'
    slugs, titles and text (a changed or added note changes the key), so the
    orb and the tools that read it compute an unchanged area once. The
    returned dicts are shared: callers read them and never change them.
    """
    key = _topics_key(notes)
    with _TOPIC_CACHE_LOCK:
        hit = _TOPIC_CACHE.get(key)
        if hit is not None:
            _TOPIC_CACHE.move_to_end(key)
            return hit
    slugs = [n.slug for n in notes]
    bags = [_bag(n) for n in notes]
    idf = _idf(bags)
    vectors = _vectors(bags, idf)
    similar = _nearest(slugs, vectors)
    label = link_communities(slugs, similar, weights=similar)
    label = _merge_fragments(slugs, label, vectors, similar)
    members: dict[str, list[int]] = defaultdict(list)
    for index, slug in enumerate(slugs):
        members[label[slug]].append(index)
    names: dict[str, str] = {}
    for lab, indexes in members.items():
        if len(indexes) >= MIN_COMMUNITY:
            names[lab] = _topic_name([bags[i] for i in indexes], idf)
    result = (label, names)
    with _TOPIC_CACHE_LOCK:
        _TOPIC_CACHE[key] = result
        while len(_TOPIC_CACHE) > _TOPIC_CACHE_SIZE:
            _TOPIC_CACHE.popitem(last=False)
    return result


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


def _degree(notes: Sequence[NoteRow]) -> Counter[str]:
    degree: Counter[str] = Counter()
    for note in notes:
        for dst in note.links:
            degree[note.slug] += 1
            degree[dst] += 1
    return degree


def note_groups(notes: Sequence[NoteRow]) -> list[NoteGroup]:
    """The orb's note communities, largest area first, largest group first.

    An area of :data:`SPLIT_ABOVE` notes or fewer is one group. A larger one
    is split by wikilinks (named after the most linked note); when that
    leaves most of it loose, the loose notes are grouped by topic
    (:func:`text_topics`, named by their shared words). Whatever is still
    alone ends in "Loose notes". Deterministic for a given set of notes.
    """
    by_area: dict[str, list[NoteRow]] = defaultdict(list)
    area_titles: dict[str, str] = {}
    for note in notes:
        aid, title = area_of(note)
        by_area[aid].append(note)
        area_titles[aid] = title
    links = {n.slug: n.links for n in notes}
    degree = _degree(notes)

    def hub(rows: Sequence[NoteRow]) -> str:
        return max(rows, key=lambda n: (degree[n.slug], n.title)).title

    out: list[NoteGroup] = []
    for aid in sorted(by_area, key=lambda a: (-len(by_area[a]), a)):
        group = sorted(by_area[aid], key=lambda n: n.slug)
        title = area_titles[aid]
        if len(group) <= SPLIT_ABOVE:
            out.append(NoteGroup(aid, title, aid, title, tuple(group), whole_area=True))
            continue
        label = link_communities([n.slug for n in group], links)
        raw: dict[str, list[NoteRow]] = defaultdict(list)
        for note in group:
            raw[label[note.slug]].append(note)
        found: dict[str, tuple[str, list[NoteRow], bool]] = {}
        loose: list[NoteRow] = []
        for lab, rows in raw.items():
            if len(rows) >= MIN_COMMUNITY:
                found[lab] = (hub(rows), rows, False)
            else:
                loose.extend(rows)
        if len(loose) * 2 > len(group):
            loose.sort(key=lambda n: n.slug)
            topic_of, names = text_topics(loose)
            by_topic: dict[str, list[NoteRow]] = defaultdict(list)
            for note in loose:
                by_topic[topic_of[note.slug]].append(note)
            loose = []
            for lab, rows in by_topic.items():
                if lab in names:
                    found["topic:" + lab] = (names[lab] or hub(rows), rows, True)
                else:
                    loose.extend(rows)
        if loose:
            found["~loose"] = ("Loose notes", loose, False)
        for lab, (name, rows, by_text) in sorted(
            found.items(), key=lambda kv: (-len(kv[1][1]), kv[0])
        ):
            out.append(NoteGroup(f"{aid}#{lab}", name, aid, title, tuple(rows), by_text=by_text))
    return out


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

    # --- Notes: vault areas, split into link and topic communities ---------
    tracked = _status_index(entities)
    degree = _degree(notes)

    def colour_of(note: NoteRow) -> str:
        return tracked.get(_norm(note.slug)) or tracked.get(_norm(note.title)) or REFERENCE

    groups = note_groups(notes)
    for aid in dict.fromkeys(g.area for g in groups):
        mine = [g for g in groups if g.area == aid]
        area_colours: list[str] = []
        sub: list[dict[str, Any]] = []
        for group in mine:
            colours = [colour_of(n) for n in group.notes]
            area_colours.extend(colours)
            if not group.whole_area:
                sub.append(
                    {
                        "id": group.id,
                        "title": group.title,
                        "level": 2,
                        "parent": aid,
                        "kind": "notes",
                        "colour": _majority(colours),
                        "reason": "",
                        "progress": None,
                        "ref": None,
                        "size": len(group.notes),
                    }
                )
            ranked = sorted(group.notes, key=lambda n: (-degree[n.slug], n.title))
            for note in ranked[:MAX_LEAVES_PER_COMMUNITY]:
                leaves.append(
                    {
                        "id": "note:" + note.slug,
                        "title": note.title,
                        "kind": note.kind or "note",
                        "colour": colour_of(note),
                        "community": group.id,
                        "ref": {"type": "note", "slug": note.slug},
                    }
                )
        communities.append(
            {
                "id": aid,
                "title": mine[0].area_title,
                "level": 1,
                "parent": None,
                "kind": "area",
                "colour": _majority(area_colours),
                "reason": "",
                "progress": None,
                "ref": None,
                "size": sum(len(g.notes) for g in mine),
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
