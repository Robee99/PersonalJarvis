"""Deterministic extraction: decisions, blockers, tasks and milestones.

The first and always-on tier (audit §8.5): keyword / regex rules over one
segment of text, English, German and Spanish. No model, no network, free on
every OS — so it is also the only tier the periodic tick runs.

Each hit is one sentence of the segment, returned with its exact character
span so the quote stored as evidence is the person's own words, verbatim.
German and Spanish literals are speech/chat INPUT vocabulary, the closed
product surface ``scripts/ci/check_no_new_german.py`` allows with an inline
``i18n-allow`` marker (the same convention as
``jarvis/memory/learning/signals.py``).

Deliberately conservative: a question is never a decision, a negated trigger
("we haven't decided", "not blocked") is skipped, and a sentence that looks
like it holds a secret yields nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from jarvis.memory.wiki.secret_guard import contains_secret

from .constants import MAX_QUOTE_CHARS, MAX_TITLE_CHARS


@dataclass(frozen=True, slots=True)
class Signal:
    """One rule hit inside a segment."""

    kind: str  # DECISION | BLOCKER | TASK | MILESTONE
    signal: str  # decided | blocked | waiting | todo | shipped | unblocked
    title: str
    quote: str  # == text[char_start:char_end]
    char_start: int
    char_end: int
    confidence: float
    urgency: int  # 0..3, explicit urgency words in the sentence
    language: str  # en | de | es


_I = re.IGNORECASE

# (kind, signal, language, pattern)
_RULES: Final[tuple[tuple[str, str, str, re.Pattern[str]], ...]] = (
    # ---- blockers ---------------------------------------------------------
    ("BLOCKER", "blocked", "en", re.compile(r"\bblocked\s+(?:on|by)\b", _I)),
    ("BLOCKER", "blocked", "en", re.compile(r"\b(?:a|the|one)\s+blocker\b", _I)),
    ("BLOCKER", "blocked", "en", re.compile(r"\bstuck\s+(?:on|with)\b", _I)),
    ("BLOCKER", "blocked", "en", re.compile(r"\bcan(?:'t|not|’t)\b[^.?!\n]{0,60}\buntil\b", _I)),
    ("BLOCKER", "waiting", "en", re.compile(r"\bwaiting\s+(?:for|on)\b", _I)),
    ("BLOCKER", "blocked", "de", re.compile(r"\bblockiert\b", _I)),  # i18n-allow
    ("BLOCKER", "blocked", "de", re.compile(r"\bkomme?n?\s+nicht\s+weiter\b", _I)),  # i18n-allow
    ("BLOCKER", "blocked", "de", re.compile(r"\bh[äa]ngt\s+an\b", _I)),  # i18n-allow
    ("BLOCKER", "waiting", "de", re.compile(r"\bwarten?\s+(?:noch\s+)?auf\b", _I)),  # i18n-allow
    ("BLOCKER", "blocked", "es", re.compile(r"\bbloquead[oa]s?\b", _I)),  # i18n-allow
    ("BLOCKER", "blocked", "es", re.compile(r"\batascad[oa]s?\b", _I)),  # i18n-allow
    (
        "BLOCKER",
        "blocked",
        "es",
        re.compile(r"\bno\s+(?:puedo|podemos)\b[^.?!\n]{0,60}\bhasta\b", _I),  # i18n-allow
    ),
    ("BLOCKER", "waiting", "es", re.compile(r"\besperando\b", _I)),  # i18n-allow
    # ---- blocker resolutions (close a blocker of the same conversation) -----
    ("BLOCKER", "unblocked", "en", re.compile(r"\b(?:unblocked|no\s+longer\s+blocked)\b", _I)),
    (
        "BLOCKER",
        "unblocked",
        "en",
        re.compile(r"\bblocker\s+(?:is\s+)?(?:resolved|gone|fixed)\b", _I),
    ),
    ("BLOCKER", "unblocked", "de", re.compile(r"\bnicht\s+mehr\s+blockiert\b", _I)),  # i18n-allow
    (
        "BLOCKER",
        "unblocked",
        "es",
        re.compile(
            r"\b(?:desbloquead[oa]s?"  # i18n-allow
            r"|ya\s+no\s+(?:estamos|est[áa]|estoy)\s+bloquead[oa]s?)\b",  # i18n-allow
            _I,
        ),
    ),
    # ---- decisions ----------------------------------------------------------
    ("DECISION", "decided", "en", re.compile(r"\b(?:we|i)(?:'ve|’ve|\s+have)?\s+decided\b", _I)),
    ("DECISION", "decided", "en", re.compile(r"\bdecided\s+to\b", _I)),
    ("DECISION", "decided", "en", re.compile(r"\blet(?:'|’)?s\s+go\s+with\b", _I)),
    ("DECISION", "decided", "en", re.compile(r"\b(?:we|i)(?:'ll|’ll|\s+will)\s+go\s+with\b", _I)),
    ("DECISION", "decided", "en", re.compile(r"\bwe(?:'re|’re|\s+are)\s+going\s+with\b", _I)),
    ("DECISION", "decided", "en", re.compile(r"\b(?:settled\s+on|the\s+decision\s+is)\b", _I)),
    ("DECISION", "decided", "de", re.compile(r"\b(?:entschieden|beschlossen)\b", _I)),  # i18n-allow
    ("DECISION", "decided", "de", re.compile(r"\bwir\s+nehmen\b", _I)),  # i18n-allow
    (
        "DECISION",
        "decided",
        "es",
        re.compile(r"\b(?:hemos\s+decidido|he\s+decidido|decidimos)\b", _I),  # i18n-allow
    ),
    (
        "DECISION",
        "decided",
        "es",
        re.compile(r"\b(?:vamos\s+con|nos\s+quedamos\s+con)\b", _I),  # i18n-allow
    ),
    # ---- tasks ----------------------------------------------------------------
    ("TASK", "todo", "en", re.compile(r"\bTODO\b")),
    ("TASK", "todo", "en", re.compile(r"\b(?:i|we)\s+(?:need|have)\s+to\b", _I)),
    ("TASK", "todo", "en", re.compile(r"\bremind\s+me\s+to\b", _I)),
    ("TASK", "todo", "en", re.compile(r"\bnext\s+steps?\b", _I)),
    ("TASK", "todo", "en", re.compile(r"\bdon(?:'|’)?t\s+forget\s+to\b", _I)),
    ("TASK", "todo", "de", re.compile(r"\b(?:ich\s+muss|wir\s+m[üu]ssen)\b", _I)),  # i18n-allow
    ("TASK", "todo", "de", re.compile(r"\berinnere\s+mich\b", _I)),  # i18n-allow
    ("TASK", "todo", "de", re.compile(r"\bn[äa]chste[rn]?\s+schritte?\b", _I)),  # i18n-allow
    ("TASK", "todo", "es", re.compile(r"\b(?:tengo|tenemos|hay)\s+que\b", _I)),  # i18n-allow
    ("TASK", "todo", "es", re.compile(r"\brecu[ée]rdame\b", _I)),  # i18n-allow
    ("TASK", "todo", "es", re.compile(r"\b(?:siguiente|pr[óo]ximo)\s+paso\b", _I)),  # i18n-allow
    # ---- milestones -----------------------------------------------------------
    (
        "MILESTONE",
        "shipped",
        "en",
        re.compile(r"\b(?:shipped|released|launched|went\s+live)\b", _I),
    ),
    ("MILESTONE", "shipped", "en", re.compile(r"\b(?:done|finished)\s+with\b", _I)),
    (
        "MILESTONE",
        "shipped",
        "de",
        re.compile(r"\b(?:ausgeliefert|ver[öo]ffentlicht)\b", _I),  # i18n-allow
    ),
    ("MILESTONE", "shipped", "de", re.compile(r"\bfertig\s+mit\b", _I)),  # i18n-allow
    (
        "MILESTONE",
        "shipped",
        "es",
        re.compile(r"\b(?:publicad[oa]|lanzad[oa])\b", _I),  # i18n-allow
    ),
    (
        "MILESTONE",
        "shipped",
        "es",
        re.compile(r"\b(?:terminamos|terminado\s+con)\b", _I),  # i18n-allow
    ),
)

#: A negation right before a trigger turns it off ("we haven't decided").
_NEGATION = re.compile(
    r"\b(?:not|never|no\s+longer|n't|haven't|hasn't|didn't|isn't|aren't"
    r"|nicht|nie|kein|keine|noch\s+nicht"  # i18n-allow
    r"|no|nunca|todav[íi]a\s+no|a[úu]n\s+no)\s*$",  # i18n-allow
    _I,
)
_NEGATION_WINDOW = 24

_URGENT = re.compile(
    r"\b(?:urgent|urgently|asap|critical|dringend|sofort|urgente|cuanto\s+antes)\b",  # i18n-allow
    _I,
)
_SOON = re.compile(
    r"\b(?:high\s+priority|important|today|tomorrow"
    r"|wichtig|heute|morgen|importante|hoy|ma[ñn]ana)\b",  # i18n-allow
    _I,
)

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n+")
# No "^": Pattern.match(text, pos) already anchors at pos, and "^" would only
# match at the very start of the text, never at a later sentence.
_LEAD_NOISE = re.compile(r"(?:[-*•>]+\s*|\d{1,3}[.)]\s+|#+\s+)")
_TODO_PREFIX = re.compile(r"^TODO\s*[:\-]?\s*")

#: A branch-like token: ``feat/brain-map``, ``fix/orb-colours``,
#: ``jarvis/local-qwen-build``. Used by clustering, never stored on its own.
_BRANCH = re.compile(
    r"\b(?:feat|feature|fix|bugfix|hotfix|chore|refactor|release|docs|claude|codex|agent|jarvis)"
    r"/[A-Za-z0-9][A-Za-z0-9._-]{1,60}\b"
)

_MIN_SENTENCE = 8
_MAX_SENTENCE = 600


def sentences(text: str) -> Iterator[tuple[int, int]]:
    """``(start, end)`` spans of the sentences in ``text``, trimmed."""
    start = 0
    for match in _SENTENCE_BOUNDARY.finditer(text):
        yield from _trim(text, start, match.start())
        start = match.end()
    yield from _trim(text, start, len(text))


def _trim(text: str, start: int, end: int) -> Iterator[tuple[int, int]]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    lead = _LEAD_NOISE.match(text, start, end)
    if lead is not None:
        start = lead.end()
    if end - start >= _MIN_SENTENCE:
        yield start, end


def _negated(sentence: str, at: int) -> bool:
    return _NEGATION.search(sentence[max(0, at - _NEGATION_WINDOW) : at]) is not None


def _title(quote: str) -> str:
    line = " ".join(_TODO_PREFIX.sub("", quote).split()).rstrip(" .!;:,")
    if len(line) > MAX_TITLE_CHARS:
        line = line[: MAX_TITLE_CHARS - 1].rsplit(" ", 1)[0] + "…"
    return line[:1].upper() + line[1:]


def urgency_of(sentence: str) -> int:
    if _URGENT.search(sentence):
        return 3
    if _SOON.search(sentence):
        return 2
    return 0


def _bounded(text: str, start: int, end: int, anchor: int) -> tuple[int, int]:
    """Cut a long sentence to ``MAX_QUOTE_CHARS`` around the trigger, at words."""
    if end - start <= MAX_QUOTE_CHARS:
        return start, end
    lo = max(start, anchor - MAX_QUOTE_CHARS // 3)
    hi = min(end, lo + MAX_QUOTE_CHARS)
    while start < lo < anchor and not text[lo - 1].isspace():
        lo += 1
    hi = min(end, lo + MAX_QUOTE_CHARS)
    while lo + 1 < hi < end and not text[hi].isspace():
        hi -= 1
    return lo, hi


def extract(text: str, *, role: str = "") -> list[Signal]:
    """Every rule hit in ``text``; at most one per kind+signal per sentence."""
    base = {"user": 0.85, "assistant": 0.6}.get(role.lower(), 0.7)
    found: list[Signal] = []
    for start, end in sentences(text):
        sentence = text[start:end]
        if len(sentence) > _MAX_SENTENCE or sentence.rstrip().endswith("?"):
            continue
        if sentence.lstrip().startswith(("```", "<!--")):
            continue
        if contains_secret(sentence):
            continue
        seen: set[tuple[str, str]] = set()
        for kind, signal, language, pattern in _RULES:
            if (kind, signal) in seen:
                continue
            match = pattern.search(sentence)
            if match is None or _negated(sentence, match.start()):
                continue
            seen.add((kind, signal))
            q_start, q_end = _bounded(text, start, end, start + match.start())
            quote = text[q_start:q_end]
            confidence = base - (0.1 if len(sentence) > 300 else 0.0)
            found.append(
                Signal(
                    kind=kind,
                    signal=signal,
                    title=_title(quote),
                    quote=quote,
                    char_start=q_start,
                    char_end=q_end,
                    confidence=round(max(0.3, confidence), 2),
                    urgency=urgency_of(sentence),
                    language=language,
                )
            )
    return found


def branch_names(text: str) -> set[str]:
    """Branch-like tokens mentioned in ``text``."""
    return {m.group(0).rstrip(".-_") for m in _BRANCH.finditer(text)}


__all__ = ["Signal", "branch_names", "extract", "sentences", "urgency_of"]
