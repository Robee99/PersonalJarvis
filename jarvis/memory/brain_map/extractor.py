"""The optional model refinement step, behind one protocol.

The deterministic rules (``rules.py``) are the complete product on every OS.
A model may add recall on top: it reads segments and PROPOSES decisions,
blockers, tasks and milestones, each with a verbatim quote. It never sets a
status, never ranks, never merges — :func:`validate` keeps only proposals
whose quote is found word for word in the segment it names, the indexer
derives status with the same rules as for everything else, and the periodic
tick never calls a model at all.

Shipped implementation: :class:`NoopExtractor`, the default. A Hermes-backed
extractor is intentionally NOT wired yet. The binding requirement for it is a
LOCAL route, a fresh session per batch and no tools, and Jarvis cannot verify
any of the three from its side today: Hermes reports only a provider slug for
the route it used (``probe_hermes`` in ``jarvis/cli_ctl/free_voice.py``), the
billing card classifies Hermes as keyless "local" even when it routes to a
cloud model (audit §3.4), and no Hermes API Jarvis models disables its tools.
An implementation must satisfy :class:`Extractor`, report ``available() is
False`` whenever those guarantees cannot be checked, and is then picked up by
``BrainMapIndexer(extractor=...)`` for on-demand runs only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

#: What a model may propose. Status-bearing native kinds are rules-only.
PROPOSABLE_KINDS: frozenset[str] = frozenset({"DECISION", "BLOCKER", "TASK", "MILESTONE"})
#: A model-only proposal never outranks a rule hit on the same words.
MODEL_CONFIDENCE_CAP = 0.7


@dataclass(frozen=True, slots=True)
class SegmentText:
    segment_id: str
    role: str
    text: str


@dataclass(frozen=True, slots=True)
class Proposal:
    segment_id: str
    kind: str
    title: str
    quote: str
    confidence: float


@dataclass(frozen=True, slots=True)
class ValidProposal:
    proposal: Proposal
    char_start: int
    char_end: int
    confidence: float


@runtime_checkable
class Extractor(Protocol):
    """A refinement step over segments the rules already processed."""

    name: str

    def available(self) -> bool:
        """True only when a call would stay local, stateless and tool-free."""
        ...

    def propose(self, segments: Sequence[SegmentText]) -> list[Proposal]: ...


class NoopExtractor:
    """The default: proposes nothing, calls nothing."""

    name = "none"

    def available(self) -> bool:
        return False

    def propose(self, segments: Sequence[SegmentText]) -> list[Proposal]:
        return []


def validate(segments: Sequence[SegmentText], proposals: Sequence[Proposal]) -> list[ValidProposal]:
    """The trust boundary: keep proposals whose quote is verbatim in its segment."""
    by_id = {s.segment_id: s for s in segments}
    kept: list[ValidProposal] = []
    for proposal in proposals:
        segment = by_id.get(proposal.segment_id)
        quote = (proposal.quote or "").strip()
        if segment is None or proposal.kind not in PROPOSABLE_KINDS or len(quote) < 8:
            continue
        start = segment.text.find(quote)
        if start < 0:
            continue
        confidence = min(MODEL_CONFIDENCE_CAP, max(0.0, float(proposal.confidence)))
        kept.append(ValidProposal(proposal, start, start + len(quote), confidence))
    return kept


__all__ = [
    "MODEL_CONFIDENCE_CAP",
    "PROPOSABLE_KINDS",
    "Extractor",
    "NoopExtractor",
    "Proposal",
    "SegmentText",
    "ValidProposal",
    "validate",
]
