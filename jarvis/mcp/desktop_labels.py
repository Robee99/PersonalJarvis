"""Turn a control's words into the point a desktop MCP server clicks.

Windows-MCP's Click, Type, Scroll and Move take ``label`` as an integer: the
position of an element in the server's own list. Its Snapshot never prints
those positions; it prints each element as a line with its centre and name::

    ├── (812,430) button "Open Roberto profile"  [action: click]

so a model reads the name and sends it as the label. Live 2026-10-26 on Gemma
4 E4B: "click on Roberto" and "click on Advanced display" were refused six
times with "field 'label' does not match any allowed form", and the turn
ended with nothing clicked. Here a worded label is looked up in the last
Snapshot the same server returned and sent as ``loc``; a label that matches
nothing is left as it is, so the refusal stands and says how to call.
"""

from __future__ import annotations

import re
from typing import Any

#: One element line of a Windows-MCP Snapshot: centre, control type, name.
_ELEMENT_LINE = re.compile(r'\((-?\d+),\s*(-?\d+)\)\s+[\w-]+\s+"([^"]*)"')
#: A window heading of the same Snapshot.
_WINDOW_LINE = re.compile(r'^\s*window\s+"([^"]*)"')

#: The words around a target that are not part of its name.
_FILLER = re.compile(
    r"\b(?:the|a|an|on|button|link|tab|icon|option|menu|item|field|box|"
    r"click|press|tap|select|choose)\b",
    re.IGNORECASE,
)

#: Snapshot tools whose answer lists the elements a later call refers to.
SNAPSHOT_TOOLS = frozenset({"Snapshot", "Snapshot-Tool", "State-Tool"})

#: Read only by servers whose pointer tools take both ``loc`` and ``label``.
_POINTER_TOOLS = frozenset({
    "Click", "Click-Tool", "Type", "Type-Tool", "Scroll", "Scroll-Tool", "Move", "Move-Tool",
})

LABEL_HINT = (
    "label is the element's number, which Snapshot does not show. Pass "
    "loc=[x, y] from the element's Snapshot line instead: for "
    '(812,430) button "Open profile" send loc [812, 430]. Take a Snapshot first '
    "if there is none."
)


def _words(text: str) -> str:
    return " ".join(re.findall(r"[0-9a-z]+", _FILLER.sub(" ", text.casefold())))


def elements(snapshot: str) -> list[tuple[int, int, str, str]]:
    """``(x, y, name, window)`` for every element line, in Snapshot order."""
    found: list[tuple[int, int, str, str]] = []
    window = ""
    for line in (snapshot or "").splitlines():
        heading = _WINDOW_LINE.match(line)
        if heading:
            window = heading.group(1)
            continue
        match = _ELEMENT_LINE.search(line)
        if match and match.group(3).strip():
            found.append((int(match.group(1)), int(match.group(2)), match.group(3), window))
    return found


def point_for(label: str, snapshot: str) -> tuple[int, int] | None:
    """The centre of the element ``label`` names, or ``None`` when none or several fit.

    An exact name wins, then a name that starts with the words, then one that
    holds them. Two different points on the best tier are ambiguous: guessing
    between them is how a click lands on the wrong control.
    """
    wanted = _words(label)
    if not wanted:
        return None
    tiers: list[list[tuple[int, int]]] = [[], [], []]
    for x, y, name, _window in elements(snapshot):
        have = _words(name)
        if not have:
            continue
        if have == wanted:
            tiers[0].append((x, y))
        elif have.startswith(wanted + " ") or have.endswith(" " + wanted):
            tiers[1].append((x, y))
        elif f" {wanted} " in f" {have} ":
            tiers[2].append((x, y))
    for points in tiers:
        distinct = list(dict.fromkeys(points))
        if len(distinct) == 1:
            return distinct[0]
        if distinct:
            return None
    return None


def takes_loc_or_label(mcp_tool_name: str, schema: dict[str, Any]) -> bool:
    """Whether a tool is a pointer tool that takes ``loc`` or an integer ``label``."""
    props = schema.get("properties") if isinstance(schema, dict) else None
    return (
        mcp_tool_name in _POINTER_TOOLS
        and isinstance(props, dict)
        and "loc" in props
        and "label" in props
    )


def worded_label(args: dict[str, Any]) -> str:
    """The label when it is words rather than an element number, else ``""``."""
    label = args.get("label")
    if not isinstance(label, str) or not label.strip():
        return ""
    return "" if label.strip().lstrip("-").isdigit() else label.strip()


def repair(args: dict[str, Any], snapshot: str) -> dict[str, Any]:
    """``args`` with a worded label replaced by the point it names, when one does."""
    label = worded_label(args)
    if not label or args.get("loc") not in (None, "", []):
        return args
    point = point_for(label, snapshot)
    if point is None:
        return args
    fixed = {key: value for key, value in args.items() if key != "label"}
    fixed["loc"] = [point[0], point[1]]
    return fixed


__all__ = [
    "LABEL_HINT",
    "SNAPSHOT_TOOLS",
    "elements",
    "point_for",
    "repair",
    "takes_loc_or_label",
    "worded_label",
]
