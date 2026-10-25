"""ReALM-style screen layout: positions kept, references numbered."""

from __future__ import annotations

from jarvis.core.protocols import UIANode
from jarvis.screen_context.screen_parse import layout


def _n(role: str, name: str, x: int, y: int, w: int = 100, h: int = 20, **kw: object) -> UIANode:
    return UIANode(role, name, bounds=(x, y, w, h), **kw)  # type: ignore[arg-type]


def test_lines_follow_the_screen_and_entities_are_numbered_in_reading_order() -> None:
    nodes = [
        _n("Button", "Call", 300, 41, 60),
        _n("Text", "Dr. Rao (206) 198 1999", 10, 40, 200),
        _n("Text", "Contacts", 10, 10),
        _n("Text", "rao@clinic.in, see www.clinic.in/hours", 10, 70, 250),
    ]

    parse = layout(nodes, max_chars=2000)

    assert parse.text.splitlines() == [
        "Contacts",
        "Dr. Rao {{1. (206) 198 1999}}\t{{2. Call}}",
        "{{3. rao@clinic.in}}, see {{4. www.clinic.in/hours}}",
    ]
    assert [(e.index, e.kind) for e in parse.entities] == [
        (1, "phone"),
        (2, "button"),
        (3, "email"),
        (4, "link"),
    ]


def test_a_control_wins_over_its_own_label_and_dates_are_not_phones() -> None:
    nodes = [
        _n("ListItem", "Mom: dinner at 8", 10, 100, 250),
        _n("Text", "Mom: dinner at 8", 12, 100, 240),
        _n("Text", "Updated 2026-10-25 21:30", 10, 130, 200),
    ]

    parse = layout(nodes, max_chars=2000)

    assert parse.text == "{{1. Mom: dinner at 8}}\nUpdated 2026-10-25 21:30"


def test_password_fields_never_reach_the_text() -> None:
    nodes = [_n("Edit", "Password", 10, 10, value="hunter22", is_password=True)]

    assert layout(nodes, max_chars=2000).text == ""


def test_a_cut_row_takes_its_entities_with_it() -> None:
    nodes = [_n("Button", "First", 10, 10), _n("Button", "Second " + "x" * 50, 10, 50)]

    parse = layout(nodes, max_chars=20)

    assert parse.truncated
    assert parse.text == "{{1. First}}"
    assert [e.text for e in parse.entities] == ["First"]


def test_in_a_browser_only_the_page_is_numbered_and_the_title_stays_on_top() -> None:
    # Shape of Chrome's tree on the PC, 2026-10-25: the window and its document
    # span the screen, the toolbar sits above the page.
    nodes = [
        _n("Window", "Clinic - Google Chrome", 0, 0, 1200, 800),
        _n("Button", "Reload", 80, 50, 30, 30),
        _n("Document", "Clinic", 0, 100, 1200, 700),
        _n("Text", "Phone: +91 80 4123 9876", 20, 400, 300),
        _n("Hyperlink", "the map page", 20, 450, 120),
    ]

    parse = layout(nodes, max_chars=2000)

    assert parse.text.splitlines() == [
        "Clinic - Google Chrome",
        "Reload",
        "Clinic",
        "Phone: {{1. +91 80 4123 9876}}",
        "{{2. the map page}}",
    ]
