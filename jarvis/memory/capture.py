""""Remember that ...": a capture answered at once, without a model.

From the owner's prompt pack (2026-10-25, prompt 05, "Total Recall"): what the
owner says after "remember that", "note to self", "don't let me forget" or
"log this" is kept two ways, and the reply is one line in character.

* **MEMORY.md** (:func:`jarvis.memory.learning.notebook.remember_explicitly`),
  which every later prompt carries, so the very next question can use it. A
  small local model that forgets to call the remember tool cannot lose it.
* **A capture note** in the vault's ``captures/`` folder, one markdown file
  per capture with the date inside, so it shows in the brain map and in
  Obsidian like any other note.

A capture never fails silently: when neither write lands, the reply says so
and why. A question ("do you remember that ...?") is not a capture.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CAPTURES_DIR = "captures"
_MAX_CHARS = 1000

_POLITE = re.compile(r"^(?:(?:please|jarvis|hey\s+jarvis|ok(?:ay)?|sir)[,\s]+)+", re.IGNORECASE)
_TRIGGER = re.compile(
    r"^(?:remember\s+that|remember\s*:|note\s+to\s+self|don'?t\s+let\s+me\s+forget(?:\s+that)?"
    r"|log\s+this|make\s+a\s+note(?:\s+that)?)[\s:,-]+(?P<fact>\S.*)$",
    re.IGNORECASE | re.DOTALL,
)

Remember = Callable[..., Any]


def capture_text(text: str) -> str | None:
    """The fact to keep, or ``None`` when ``text`` is not a capture."""
    phrase = _POLITE.sub("", " ".join(str(text or "").split()))
    match = _TRIGGER.match(phrase)
    if not match:
        return None
    fact = match.group("fact").strip().rstrip(".!").strip()
    if not fact or fact.endswith("?"):
        return None
    return (fact[0].upper() + fact[1:])[:_MAX_CHARS]


def _slug(fact: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", fact.lower())[:6]
    return "-".join(words) or "capture"


def write_capture(vault_root: Path, fact: str, *, now: datetime) -> Path:
    """One markdown note under ``captures/``; never overwrites an earlier one."""
    folder = vault_root / CAPTURES_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{now:%Y-%m-%d}-{_slug(fact)}"
    path = folder / f"{stem}.md"
    n = 2
    while path.exists():
        path = folder / f"{stem}-{n}.md"
        n += 1
    title = " ".join(fact.split()[:8])
    path.write_text(
        f"---\ntype: capture\ncreated: {now:%Y-%m-%d %H:%M}\n---\n\n# {title}\n\n{fact}\n",
        encoding="utf-8",
    )
    return path


def _remember_default(fact: str, *, evidence: str) -> Any:
    from jarvis.memory.learning.notebook import remember_explicitly

    return remember_explicitly(fact, evidence=evidence, source="capture reflex")


def capture_reflex(
    text: str,
    *,
    vault_root: Path | None,
    honorific: str = "Sir",
    remember: Remember = _remember_default,
    clock: Callable[[], datetime] = datetime.now,
) -> str | None:
    """Keep a "remember that ..." and say so in one line, or ``None``."""
    fact = capture_text(text)
    if fact is None:
        return None
    h = honorific or "friend"
    problems: list[str] = []
    kept = False
    try:
        remember(fact, evidence=text)
        kept = True
    except ValueError as exc:
        # The guard refuses secrets and oversized text: nothing is written.
        return f"I will not write that down, {h}: {exc}."
    except LookupError as exc:  # Notebook unavailability is included in the reply below.
        problems.append(f"the memory notebook is not running ({exc})")
    except OSError as exc:  # Notebook write failures are included in the reply below.
        problems.append(f"the memory notebook could not be written ({exc})")
    if vault_root is not None:
        try:
            write_capture(vault_root, fact, now=clock())
            kept = True
        except OSError as exc:  # Capture write failures are included in the reply below.
            problems.append(f"the capture note could not be written ({exc})")
    else:
        problems.append("no notes folder is configured")
    if not kept:
        log.warning("capture lost: %s", "; ".join(problems))
        return f"I could not save that, {h}: " + "; ".join(problems) + "."
    if problems:
        log.warning("capture partly saved: %s", "; ".join(problems))
    return f"Noted, {h}."
