"""Vocabulary of the brain map — the one Python source of truth.

Every tuple here is mirrored by a ``CHECK`` list in
``jarvis/memory/migrations/0010_brain_map.sql`` and by a Pydantic ``Literal``
in ``jarvis/ui/web/brain_map_routes.py``; ``tests/unit/memory/brain_map/
test_parity.py`` keeps the three in step (AP-4). The Orb's TypeScript union
joins the parity test when the frontend lands.
"""

from __future__ import annotations

from typing import Final, Literal

EntityKind = Literal[
    "PROJECT",
    "CONVERSATION",
    "THREAD",
    "TASK",
    "DECISION",
    "BLOCKER",
    "MILESTONE",
    "DOCUMENT",
    "AGENT",
    "OUTPUT",
]
ENTITY_KINDS: Final[tuple[str, ...]] = (
    "PROJECT",
    "CONVERSATION",
    "THREAD",
    "TASK",
    "DECISION",
    "BLOCKER",
    "MILESTONE",
    "DOCUMENT",
    "AGENT",
    "OUTPUT",
)

#: red = blocked / failed / overdue; blue = active; yellow = waiting, stale or
#: needs attention; green = done, verified or closed with nothing left to do.
Status = Literal["red", "blue", "yellow", "green"]
STATUSES: Final[tuple[str, ...]] = ("red", "blue", "yellow", "green")

RelationKind = Literal["part_of", "mentions", "blocks", "decided_in", "produced", "follows_from"]
RELATION_KINDS: Final[tuple[str, ...]] = (
    "part_of",
    "mentions",
    "blocks",
    "decided_in",
    "produced",
    "follows_from",
)

Assignment = Literal["confirmed", "suggested"]
ASSIGNMENTS: Final[tuple[str, ...]] = ("confirmed", "suggested")

Origin = Literal["native", "extracted", "derived"]
ORIGINS: Final[tuple[str, ...]] = ("native", "extracted", "derived")

SourceKind = Literal[
    "agent_chat",
    "goal",
    "mission",
    "output",
    "quest",
    "automation",
    "ide_project",
    "ide_thread",
    "cli_session",
    "wiki_project",
    "wiki_import",
]
SOURCE_KINDS: Final[tuple[str, ...]] = (
    "agent_chat",
    "goal",
    "mission",
    "output",
    "quest",
    "automation",
    "ide_project",
    "ide_thread",
    "cli_session",
    "wiki_project",
    "wiki_import",
)

#: Bumped whenever the deterministic rules change, so every segment is
#: re-read once under the new rules (its content hash alone would not change).
RULES_VERSION: Final[str] = "rules:v1"

#: Below this a project assignment is kept, but only as a suggestion: shown
#: hollow, left out of counts and priorities until something confirms it.
CONFIDENCE_FLOOR: Final[float] = 0.6

DAY_MS: Final[int] = 24 * 3600 * 1000
#: A task, thread or conversation without activity for this long is stale.
STALE_ITEM_MS: Final[int] = 7 * DAY_MS
#: A blocker not mentioned again for this long is asked about, not assumed.
STALE_BLOCKER_MS: Final[int] = 30 * DAY_MS
#: A project with no activity this long is stale (wiki schema: dormant at 90).
STALE_PROJECT_MS: Final[int] = 90 * DAY_MS
#: "Active" means touched within this window.
ACTIVE_WINDOW_MS: Final[int] = 7 * DAY_MS

#: Longest quote kept as evidence. Longer sentences are cut at a word.
MAX_QUOTE_CHARS: Final[int] = 240
MAX_TITLE_CHARS: Final[int] = 120
