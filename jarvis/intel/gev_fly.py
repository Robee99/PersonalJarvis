"""Fly God's Eye View to a named place without a model choosing the tool.

Live 2026-10-27 "fly me to Mumbai in God's Eye View" reached Gemma with eight
of GEV's thirty data tools, and it answered with rocket launches: "it does not
have a function to fly you to a specific city". GEV's own
``show_in_gods_eye_view`` geocodes a place and the MCP adapter flies the World
screen's globe to its answer (jarvis/intel/gev_view.py). A plain "fly / take
me / go / zoom to <place>" that names GEV or the globe calls it directly.

Questions, negations and a second job after the place stay with the brain.
"""

from __future__ import annotations

import re
from typing import Any

from jarvis.intel.gev_view import GEV_VIEW_TOOL

_GEV = (
    r"(?:(?:the\s+)?gods?['’]?\s*s?\s*eye(?:\s+view)?(?:\s+globe)?|(?:the\s+)?gev"
    r"|(?:the\s+)?globe|(?:the\s+)?world\s+(?:screen|map|view))"
)
_LEAD = (
    r"^\s*(?:(?:please|jarvis|hey\s+jarvis|hi|hey|ok(?:ay)?|now|can\s+you|could\s+you)[,\s]+)*"
    rf"(?:(?:open|go\s+to|launch|use|in|on)\s+{_GEV}\s*(?:,|\band\b|\bthen\b)?\s*)?"
)
_MOVE = (
    r"(?:fly|take|bring|go|zoom|navigate|travel|move|jump|head)"
    r"(?:\s+(?:me|us|the\s+globe|the\s+camera|it|in))?\s+(?:to|over|into|on)"
    # "show me earthquakes in GEV" is a data ask, so "show" is not a move here.
    r"|centre\s+on|center\s+on"
)
_FLY = re.compile(
    _LEAD
    + rf"(?:{_MOVE})\s+(?P<place>.+?)"
    + rf"(?:\s+(?:in|on|using|with)\s+{_GEV})?"
    + r"\s*(?:please|now)?[.!\s]*$",
    re.IGNORECASE,
)
_NOT_PLAIN = re.compile(
    r"\?|\b(?:don'?t|do\s+not|never|if|unless|until|later|tomorrow|and\s+then|then)\b",
    re.IGNORECASE,
)
_SAYS_GEV = re.compile(rf"\b{_GEV}\b", re.IGNORECASE)


def parse_fly_request(text: str) -> str | None:
    """The place this utterance plainly asks God's Eye View to fly to, else ``None``."""
    text = (text or "").strip()
    if not text or len(text) > 200 or not _SAYS_GEV.search(text):
        return None
    m = _FLY.match(text)
    if m is None:
        return None
    place = m.group("place").strip(" .,!")
    if not place or _NOT_PLAIN.search(place) or _NOT_PLAIN.search(text.replace(place, "")):
        return None
    if _SAYS_GEV.fullmatch(place) or re.search(r"\band\b.+\b(?:open|play|click|close)\b", place):
        return None
    return place


def view_tool(tools: dict[str, Any]) -> Any | None:
    """GEV's ``show_in_gods_eye_view`` among the registered tools, or ``None``."""
    return next(
        (tool for name, tool in tools.items() if name.rsplit("/", 1)[-1] == GEV_VIEW_TOOL),
        None,
    )


def fly_reply(place: str, output: Any) -> str:
    """What to say after the call, from GEV's own answer."""
    items = output if isinstance(output, list) else [output]
    texts = [str(i) for i in items if isinstance(i, str)]
    shown = any(t.startswith("Shown now") for t in texts)
    label = place
    for t in texts:
        found = re.match(r"\s*Open\s+(?P<label>.+?)\s+in God's Eye View", t)
        if found:
            label = found.group("label")
            break
    if shown:
        return f"Flying the globe to {label} in God's Eye View."
    return f"God's Eye View found {label}, but the World screen is not open to show it."


__all__ = ["fly_reply", "parse_fly_request", "view_tool"]
