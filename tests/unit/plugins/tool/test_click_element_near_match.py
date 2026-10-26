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


PICKER = [_Node("Nancy Bhaju"), _Node("Roberto Bhaju"), _Node("Add"), _Node("Guest mode")]


def test_a_misheard_name_takes_the_closest_label() -> None:
    (hit,) = matching_nodes(PICKER, name="roboto")
    assert hit.name == "Roberto Bhaju"


def test_an_exact_substring_still_wins() -> None:
    assert [n.name for n in matching_nodes(PICKER, name="nancy")] == ["Nancy Bhaju"]


def test_nothing_close_clicks_nothing() -> None:
    assert matching_nodes(PICKER, name="settings") == []


def test_two_equally_close_labels_are_ambiguous() -> None:
    assert matching_nodes([_Node("Kati"), _Node("Katy")], name="kate") == []


def test_a_role_filter_applies_to_the_near_match() -> None:
    assert matching_nodes(PICKER, name="roboto", role="link") == []
