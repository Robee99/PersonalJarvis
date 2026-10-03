"""Where the pointing arrow goes. Pure arithmetic, no Qt, so it is testable
on every CI runner (the renderer that draws it needs a GUI stack)."""

from __future__ import annotations

_POINT_GAP_PX = 12.0
_POINT_LENGTH_PX = 150.0


def arrow_geometry(
    width: float, height: float, target: tuple[float, float, float, float]
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    """``(tip, tail, direction)`` for an arrow aimed at ``target`` (x, y, w, h).

    The arrow comes from the inside of the screen, so it never leaves the
    monitor even when the element hugs an edge, and its tip stops just
    outside the element so the label stays readable. ``direction`` is the
    unit vector from the tip towards the tail.
    """
    x, y, w, h = target
    cx, cy = x + w / 2.0, y + h / 2.0
    dx, dy = width / 2.0 - cx, height / 2.0 - cy
    norm = (dx * dx + dy * dy) ** 0.5
    if norm < 1.0:
        # Dead centre: come in from the lower right, like a hand would.
        dx, dy, norm = 1.0, 1.0, 2.0**0.5
    ux, uy = dx / norm, dy / norm
    # Distance from the centre to the element's border along the direction.
    reach_x = (w / 2.0) / abs(ux) if abs(ux) > 1e-6 else float("inf")
    reach_y = (h / 2.0) / abs(uy) if abs(uy) > 1e-6 else float("inf")
    reach = min(reach_x, reach_y) + _POINT_GAP_PX
    tip = (cx + ux * reach, cy + uy * reach)
    tail = (tip[0] + ux * _POINT_LENGTH_PX, tip[1] + uy * _POINT_LENGTH_PX)
    return tip, tail, (ux, uy)


__all__ = ["arrow_geometry"]
