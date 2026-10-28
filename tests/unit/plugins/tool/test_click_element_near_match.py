"""click_element takes the one closest label when speech garbled the name.

Live 2026-10-26: "click on roboto" on Chrome's profile picker, where the
profile is "Roberto"; no label contains "roboto", so nothing was clicked.
"""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.plugins.tool.click_element import matching_nodes


@dataclass
class _Node:
    name: str
    role: str = "button"
    enabled: bool = True
    bounds: tuple[int, int, int, int] = (0, 0, 10, 10)
    automation_id: str = ""


#: The picker's UIA labels as Chrome exposes them (read live 2026-10-26).
PICKER = [
    _Node("Who's using Chrome?", role="text"),
    _Node("Open Nancy profile"),
    _Node("Rename your profile", role="edit"),
    _Node("More actions for Nancy"),
    _Node("Open Roberto profile"),
    _Node("Rename your profile", role="edit"),
    _Node("More actions for Roberto"),
    _Node("Add"),
    _Node("Guest mode"),
]


def test_a_misheard_name_takes_the_closest_word_in_tree_order() -> None:
    hits = matching_nodes(PICKER, name="roboto")
    assert [n.name for n in hits] == ["Open Roberto profile", "More actions for Roberto"]


def test_an_exact_substring_still_wins() -> None:
    hits = matching_nodes(PICKER, name="nancy")
    assert [n.name for n in hits] == ["Open Nancy profile", "More actions for Nancy"]


def test_nothing_close_clicks_nothing() -> None:
    assert matching_nodes(PICKER, name="settings") == []


def test_two_different_words_equally_close_are_ambiguous() -> None:
    assert matching_nodes([_Node("Kati"), _Node("Katy")], name="kate") == []


def test_a_role_filter_applies_to_the_near_match() -> None:
    assert matching_nodes(PICKER, name="roboto", role="link") == []
