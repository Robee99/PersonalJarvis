"""Two-step navigation commands: open a section, then a control inside it.

``match_navigation_followup("open settings and go to the model page")`` →
``NavigationFollowup(section="settings", label="model")``. The deterministic
navigation fast path (``BrainManager._run_navigation_fast_path``) opens the
section and then clicks the control by its words, through ``navigate``'s
``then`` argument, with no model call.

Conservative, like ``match_navigation_intent``: the utterance must split at a
connector ("and", "then", a comma, "und dann", "y luego"), the part before it
must be a navigation command on its own, and the part after it must start with
a second cue ("go to", "show", "click", ...) followed by a short label
(at most four words once filler like "the", "page", "tab" is dropped). Anything
longer is a sentence about the screen, not the name of a control, and the
turn falls back to plain navigation. Pure regex, no LLM, no IO (AP-9/AP-11).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from jarvis.brain.navigation_intent import match_navigation_intent

MAX_LABEL_WORDS = 4

# Where the second instruction starts. Longest alternatives first, so
# "and then" is one connector rather than "and" followed by a "then" label.
_JOIN = (
    r"(?:and\s+then|and|then"
    r"|und\s+dann|und|dann"  # i18n-allow: input vocab
    r"|y\s+luego|y\s+después|y|luego)"  # i18n-allow: input vocab
)
_CONNECTOR = re.compile(rf"\s*,\s*{_JOIN}\s+|\s+{_JOIN}\s+|\s*,\s*", re.I)

# The second instruction's verb. Matching input vocabulary, not prose.
_FOLLOW_CUE = re.compile(
    r"^(?:"
    r"go\s+to|switch\s+to|navigate\s+to|jump\s+to|take\s+me\s+to|head\s+to"
    r"|open(?:\s+up)?|show(?:\s+me)?|click(?:\s+on)?|tap(?:\s+on)?|press|hit"
    r"|select|choose|pick"
    r"|geh(?:e)?\s+(?:zu|auf|in|zur|zum)"  # i18n-allow: input vocab
    r"|wechs(?:le|el)\s+(?:zu|auf|in|zur|zum)"  # i18n-allow: input vocab
    r"|öffne|oeffne|zeig(?:e)?(?:\s+mir)?"  # i18n-allow: input vocab
    r"|klick(?:e)?(?:\s+auf)?|drück(?:e)?(?:\s+auf)?"  # i18n-allow: input vocab
    r"|wähle|waehle"  # i18n-allow: input vocab
    r"|ve\s+a|abre|muestra(?:me)?|haz\s+clic\s+en|pulsa|selecciona"  # i18n-allow: input vocab
    r")\b",
    re.I,
)

# Words that wrap a label without being part of it.
# fmt: off
_FILLER = frozenset(
    {
        "the", "a", "an", "page", "tab", "section", "screen", "view", "panel",
        "button", "please", "now", "there", "its", "my", "subpage", "sub-page",
        "den", "die", "das", "der", "dem",  # i18n-allow: input vocab
        "seite", "reiter", "bereich", "bitte",  # i18n-allow: input vocab
        "mal", "dort", "da",  # i18n-allow: input vocab
        "el", "la", "los", "las",  # i18n-allow: input vocab
        "pestaña", "página", "sección", "pantalla",  # i18n-allow: input vocab
        "por", "favor",  # i18n-allow: input vocab
    }
)
# fmt: on


# Words that turn the tail into a question or a request about the screen ("show
# me how to change the voice"), which no single control answers.
# fmt: off
_NOT_A_LABEL = frozenset(
    {
        "how", "what", "why", "which", "where", "when", "whether", "if", "me",
        "wie", "was", "warum", "wieso",  # i18n-allow: input vocab
        "welche", "welcher", "wo", "wann", "ob",  # i18n-allow: input vocab
        "cómo", "qué", "cuál", "dónde", "cuándo",  # i18n-allow: input vocab
    }
)
# fmt: on


@dataclass(frozen=True)
class NavigationFollowup:
    """A section to open and the label of the control to click inside it."""

    section: str
    label: str


def _label(tail: str) -> str | None:
    cue = _FOLLOW_CUE.match(tail)
    if cue is None:
        return None
    words = [
        word
        for word in re.split(r"\s+", tail[cue.end() :].strip(" \t.!?;:\"'"))
        if word and word.lower() not in _FILLER
    ]
    if not words or len(words) > MAX_LABEL_WORDS:
        return None
    if any(word.lower().strip(",") in _NOT_A_LABEL for word in words):
        return None
    return " ".join(words)


def match_navigation_followup(text: str) -> NavigationFollowup | None:
    """Section plus a control label for "open X and go to Y", else None."""
    t = " ".join((text or "").strip().split())
    if not t:
        return None
    for connector in _CONNECTOR.finditer(t):
        head, tail = t[: connector.start()], t[connector.end() :]
        section = match_navigation_intent(head)
        if section is None:
            continue
        label = _label(tail)
        if label is not None:
            return NavigationFollowup(section=section, label=label)
    return None


__all__ = ["MAX_LABEL_WORDS", "NavigationFollowup", "match_navigation_followup"]
