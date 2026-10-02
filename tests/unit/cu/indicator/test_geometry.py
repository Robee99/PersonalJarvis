"""Pointing-arrow placement: from inside the screen, stopping short of the element."""

from __future__ import annotations

from jarvis.cu.indicator.geometry import arrow_geometry


def test_arrow_comes_from_inside_the_screen_and_stops_short_of_the_element() -> None:
    # Element hugging the top-right corner: the arrow must come from below-left.
    (tip_x, tip_y), (tail_x, tail_y), (ux, uy) = arrow_geometry(
        1000.0, 800.0, (900.0, 0.0, 100.0, 40.0)
    )
    assert ux < 0 < uy
    assert 0.0 <= tail_x <= 1000.0 and 0.0 <= tail_y <= 800.0
    # The tip sits just outside the element, never on top of its label.
    assert not (900.0 <= tip_x <= 1000.0 and 0.0 <= tip_y <= 40.0)

    # Element at dead centre still gets an arrow.
    tip, tail, direction = arrow_geometry(1000.0, 800.0, (450.0, 380.0, 100.0, 40.0))
    assert direction[0] > 0 and direction[1] > 0
