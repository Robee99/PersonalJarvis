"""The screen as laid-out text with numbered entities (Apple's ReALM method).

ReALM ("Reference Resolution As Language Modeling", Apple 2024) showed that a
small model resolves "call this number" or "open the second link" about as
well as a large vision model when the screen reaches it as text that keeps the
layout. Each object's centre is placed, objects are sorted top to bottom and
then left to right, objects within a small vertical margin share a line,
objects on one line are joined by tabs, and every thing the user can refer to
is wrapped as ``{{n. text}}``. Apple published the method, not code; this is
that method over the accessibility tree Jarvis already reads.

It matters most for the local brains. Gemma 4 E4B reads small screenshot text
poorly, and Nemotron 3.5 Bolt E4B cannot see an image at all, so for Nemotron
this text is the whole screen.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

#: Accessibility roles a person can point at, and what to call them.
_ENTITY_ROLES: dict[str, str] = {
    "Button": "button",
    "SplitButton": "button",
    "Hyperlink": "link",
    "MenuItem": "menu item",
    "TabItem": "tab",
    "CheckBox": "checkbox",
    "RadioButton": "option",
    "ComboBox": "dropdown",
    "Edit": "field",
    "ListItem": "item",
    "TreeItem": "item",
    "DataItem": "item",
}
#: Chrome, never content (the same list as uitext's).
_CHROME_ROLES = frozenset({"ScrollBar", "Separator", "Thumb", "TitleBar", "Splitter"})
#: Things inside ordinary text that a person names by kind: "this number".
_TEXT_ENTITIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("link", re.compile(r"\bhttps?://[^\s<>\"']+|\bwww\.[\w-]+(?:\.[\w-]+)+[^\s<>\"']*")),
    ("phone", re.compile(r"(?<![\w+])\+?\(?\d[\d\s().-]{7,}\d(?!\w)")),
)
#: Roles that hold other things; placed by their top line, never their centre.
_CONTAINER_ROLES = frozenset({"Window", "Pane", "Document", "Group", "Custom", "Application"})
_CONTAINER_LINE_PX = 24
_MAX_NODE_CHARS = 300
_MAX_ENTITIES = 80


@dataclass(frozen=True, slots=True)
class Entity:
    index: int
    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class ScreenParse:
    text: str
    entities: tuple[Entity, ...]
    truncated: bool


@dataclass(slots=True)
class _Item:
    text: str
    kind: str | None
    cx: float
    cy: float
    height: int
    order: int
    in_content: bool = True


def _label(node: Any) -> str:
    name = " ".join(str(getattr(node, "name", "") or "").split())
    value = " ".join(str(getattr(node, "value", "") or "").split())
    if value and value != name:
        text = f"{name}: {value}" if name else value
    else:
        text = name
    return text[:_MAX_NODE_CHARS]


_DATE_LIKE = re.compile(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}[-./]\d{1,2}[-./]\d{2,4}")


def _is_phone(match: str) -> bool:
    """8 to 15 digits that are not a date ("2026-10-25 21")."""
    return 8 <= sum(ch.isdigit() for ch in match) <= 15 and not _DATE_LIKE.search(match)


def is_placed(node: Any) -> bool:
    """Whether the node has an on-screen box to lay it out by."""
    bounds = tuple(getattr(node, "bounds", (0, 0, 0, 0)) or (0, 0, 0, 0))
    return len(bounds) >= 4 and bounds[2] > 0 and bounds[3] > 0


def _content_box(nodes: list[Any]) -> tuple[int, int, int, int] | None:
    """The largest document's box: a browser's page, not its tabs and toolbar."""
    docs = [
        tuple(n.bounds)[:4] for n in nodes if getattr(n, "role", "") == "Document" and is_placed(n)
    ]
    return max(docs, key=lambda b: b[2] * b[3]) if docs else None


def _inside(box: tuple[int, int, int, int] | None, cx: float, cy: float) -> bool:
    if box is None:
        return True
    x, y, w, h = box
    return x <= cx <= x + w and y <= cy <= y + h


def _items(nodes: Iterable[Any]) -> list[_Item]:
    """One item per distinct visible label; a control wins over its own text.

    A container (window, pane, document) is placed at its top line, where its
    title shows, not at its centre in the middle of the content it holds. With
    a document on screen only what is inside it is numbered: "the second
    link" means the page's, not the browser toolbar's.
    """
    nodes = list(nodes)
    content = _content_box(nodes)
    best: dict[str, _Item] = {}
    for order, node in enumerate(nodes):
        if bool(getattr(node, "is_password", False)):
            continue
        role = str(getattr(node, "role", "") or "")
        if role in _CHROME_ROLES:
            continue
        text = _label(node)
        if not text:
            continue
        placed = is_placed(node)
        x, y, w, h = tuple(node.bounds)[:4] if placed else (0, 0, 0, 0)
        if placed and role in _CONTAINER_ROLES:
            h = min(h, _CONTAINER_LINE_PX)
            cx, cy = float(x), y + h / 2
        elif placed:
            cx, cy = x + w / 2, y + h / 2
        else:
            # Unbounded nodes go last, in tree order (no position to sort by).
            cx = cy = float("inf")
        in_content = placed and _inside(content, cx, cy) or (content is None)
        item = _Item(
            text=text,
            kind=_ENTITY_ROLES.get(role) if in_content else None,
            cx=cx,
            cy=cy,
            height=h if placed else 0,
            order=order,
            in_content=in_content,
        )
        seen = best.get(text)
        if seen is None or (seen.kind is None and item.kind is not None):
            best[text] = item
    return list(best.values())


def _lines(items: list[_Item]) -> list[list[_Item]]:
    """ReALM's grouping: sort by centre, one line per vertical margin."""
    heights = [i.height for i in items if i.height > 0]
    margin = max(4.0, statistics.median(heights) / 2) if heights else 4.0
    ordered = sorted(items, key=lambda i: (i.cy, i.cx, i.order))
    lines: list[list[_Item]] = []
    anchor = None
    for item in ordered:
        if lines and anchor is not None and abs(item.cy - anchor) <= margin:
            lines[-1].append(item)
        else:
            lines.append([item])
            anchor = item.cy
    return [sorted(line, key=lambda i: (i.cx, i.order)) for line in lines]


def layout(nodes: Iterable[Any], *, max_chars: int, first_index: int = 1) -> ScreenParse:
    """The screen as tab-separated lines with ``{{n. text}}`` entities.

    ``first_index`` continues a numbering already on the page, so OCR text
    added under the accessibility text never reuses one of its numbers.
    """
    entities: list[Entity] = []

    def tag(kind: str, text: str) -> str:
        if len(entities) >= _MAX_ENTITIES:
            return text
        index = first_index + len(entities)
        entities.append(Entity(index, kind, text))
        return f"{{{{{index}. {text}}}}}"

    def render(item: _Item) -> str:
        if item.kind is not None:
            return tag(item.kind, item.text)
        if not item.in_content:
            return item.text
        out, pos = [], 0
        spans = sorted(
            (m.start(), m.end(), kind)
            for kind, pattern in _TEXT_ENTITIES
            for m in pattern.finditer(item.text)
            if kind != "phone" or _is_phone(m.group())
        )
        for start, end, kind in spans:
            if start < pos:
                continue  # an email's domain is not also a link
            out.append(item.text[pos:start])
            out.append(tag(kind, item.text[start:end].strip()))
            pos = end
        out.append(item.text[pos:])
        return "".join(out)

    rows: list[str] = []
    total = 0
    truncated = False
    for line in _lines(_items(nodes)):
        row = "\t".join(render(item) for item in line)
        if total + len(row) + 1 > max_chars:
            truncated = True
            break
        rows.append(row)
        total += len(row) + 1
    kept = "\n".join(rows)
    # An entity cut off with its row is not on the page the model reads.
    shown = tuple(e for e in entities if f"{{{{{e.index}. " in kept)
    return ScreenParse(text=kept, entities=shown, truncated=truncated)


_ENTITY_MARK = re.compile(r"\{\{(\d+)\. ")


def last_entity_index(text: str) -> int:
    """The highest ``{{n. `` number in ``text``, 0 when it has none."""
    return max((int(m.group(1)) for m in _ENTITY_MARK.finditer(text)), default=0)


#: The line that teaches the model the notation, once per screen.
ENTITY_LEGEND = (
    "Lines keep the screen's layout (tabs separate things side by side). "
    "{{n. text}} marks something the user can point at: a button, link, field, "
    "item, phone number, email or web address. When the user says this, that, "
    "it, or 'the second one', they mean one of these. Never say the braces or "
    "the number aloud; say the thing itself."
)


__all__ = [
    "ENTITY_LEGEND",
    "Entity",
    "ScreenParse",
    "is_placed",
    "last_entity_index",
    "layout",
]
