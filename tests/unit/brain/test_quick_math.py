"""Plain arithmetic is answered exactly; Gemma said 395.8 for 397.8 (2026-10-25)."""

from __future__ import annotations

import pytest

from jarvis.brain.quick_math import solve


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("What is 17 percent of 2340?", "17 percent of 2,340 is 397.8."),
        ("17% of 2,340", "17 percent of 2,340 is 397.8."),
        ("what is 12 times 3", "12 times 3 is 36."),
        ("Calculate 2.5 plus 4 divided by 2.", "2.5 plus 4 divided by 2 is 4.5."),
        ("what's (3 + 4) x 2?", "(3 plus 4) times 2 is 14."),
        ("What is the square root of 81?", "The square root of 81 is 9."),
        ("how much is 10 divided by 4", "10 divided by 4 is 2.5."),
        ("what is 5 divided by 0", "That divides by zero, so it has no answer."),
    ],
)
def test_plain_arithmetic_is_worked_out(text: str, expected: str) -> None:
    assert solve(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "what is 3 times the price of milk",
        "what is 2026",
        "what time is it",
        "set the volume to 40",
        "open 7 zip",
        "what is 2 ** 10000000",
        "what is __import__('os')",
    ],
)
def test_anything_else_goes_to_the_brain(text: str) -> None:
    assert solve(text) is None
