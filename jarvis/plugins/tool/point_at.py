"""point_at tool: a glowing arrow lands on one UI element. It never clicks.

The answer to "where do I click to ...?" is an arrow on the screen, not a
description of one. The tool reads the foreground window's accessibility tree
through the same per-OS source and the same matching rules as
``click_element`` (``jarvis.vision.tree_factory``), so it aims at exactly the
element ``click_element`` would press. The arrow is drawn by the Computer-Use
indicator sidecar (``jarvis.cu.indicator``), which is click-through, excluded
from screen capture on Windows and blanked before every grab elsewhere, and
fades out on its own.

Risk tier: safe. It reads the UI tree the router already reads for
``inspect-pointer`` and draws a transient overlay; nothing in any app changes.
"""

from __future__ import annotations

import logging
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.plugins.tool.click_element import matching_nodes, visible_labels

log = logging.getLogger(__name__)

Monitor = tuple[int, int, int, int]


def _rank(nodes: list[Any], needle: str) -> list[Any]:
    """Exact label first, then a label starting with the needle, then the rest."""
    needle = needle.lower()

    def score(node: Any) -> int:
        label = (node.name or "").strip().lower()
        if label == needle:
            return 0
        if label.startswith(needle):
            return 1
        return 2

    return sorted(nodes, key=score)


def _mss_monitors() -> list[Monitor]:
    """Every physical monitor in capture coordinates (UIA/AX bounds use the same)."""
    import mss  # noqa: PLC0415

    with mss.mss() as sct:
        return [(m["left"], m["top"], m["width"], m["height"]) for m in sct.monitors[1:]]


def monitor_and_fraction(
    bounds: tuple[int, int, int, int], monitors: list[Monitor]
) -> tuple[Monitor, list[float]] | None:
    """The monitor holding the element's centre and the element as fractions of it."""
    x, y, w, h = bounds
    cx, cy = x + w / 2.0, y + h / 2.0
    for monitor in monitors:
        left, top, width, height = monitor
        if width > 0 and height > 0 and left <= cx < left + width and top <= cy < top + height:
            return monitor, [
                (x - left) / width,
                (y - top) / height,
                w / width,
                h / height,
            ]
    return None


class PointAtTool:
    name: str = "point_at"
    risk_tier: str = "safe"
    description: str = (
        "Shows the user WHERE something is on the screen: a glowing arrow lands on "
        "the named UI element (button, menu, field, tab) of the foreground window. "
        "Never clicks. Use it when the user asks where to click or where something "
        "is; use computer_use when they want it done."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Visible label of the element, matched case-insensitively",
            },
            "role": {
                "type": "string",
                "description": "Optional control type (e.g. Button, MenuItem, Edit)",
            },
            "nth": {
                "type": "integer",
                "default": 0,
                "description": "When several elements match, pick the nth (0-based)",
            },
        },
        "required": ["name"],
    }

    def __init__(
        self,
        vision_source: Any | None = None,
        monitors: Any | None = None,
        controller: Any | None = None,
    ) -> None:
        self._vision_source = vision_source
        self._monitors = monitors or _mss_monitors
        self._controller = controller

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        needle = str((args or {}).get("name") or "").strip()
        role = str((args or {}).get("role") or "").strip()
        try:
            nth = max(int((args or {}).get("nth", 0)), 0)
        except (TypeError, ValueError):  # a non-numeric index means the first match
            nth = 0
        if not needle:
            return ToolResult(success=False, output=None, error="Provide the element's 'name'")

        controller = self._controller
        if controller is None:
            from jarvis.cu.indicator.controller import get_indicator_controller  # noqa: PLC0415

            controller = get_indicator_controller()
        if controller is None:
            return ToolResult(
                success=False,
                output=None,
                error="The on-screen overlay is not running in this session.",
            )

        source = self._vision_source
        if source is None:
            from jarvis.vision.tree_factory import make_ui_tree_source  # noqa: PLC0415

            source = make_ui_tree_source()
        try:
            obs = await source.observe()
        except Exception as exc:  # noqa: BLE001 - UIA/AX/AT-SPI errors are varied
            log.warning("point_at: UI tree observation failed", exc_info=True)
            return ToolResult(success=False, output=None, error=f"Reading the UI failed: {exc}")

        candidates = _rank(matching_nodes(obs.nodes, name=needle, role=role), needle)
        if not candidates:
            return ToolResult(
                success=False,
                output=None,
                error=(
                    f"No element labelled {needle!r} in the foreground window. "
                    f"Visible labels: {visible_labels(obs.nodes)}"
                ),
            )
        node = candidates[min(nth, len(candidates) - 1)]

        try:
            monitors = self._monitors()
        except Exception as exc:  # noqa: BLE001 - mss/display errors are varied
            log.warning("point_at: monitor enumeration failed", exc_info=True)
            return ToolResult(
                success=False, output=None, error=f"Could not read the monitors: {exc}"
            )
        placed = monitor_and_fraction(tuple(node.bounds), monitors)
        if placed is None:
            return ToolResult(
                success=False,
                output=None,
                error=f"{node.name!r} is not on any visible monitor.",
            )
        monitor, rect = placed

        label = (node.name or needle).strip()
        shown, reason = await controller.point(monitor=list(monitor), rect=rect, label=label)
        if not shown:
            return ToolResult(
                success=False, output=None, error=f"Could not draw the arrow: {reason}"
            )
        others = f" ({len(candidates)} matches, showing #{min(nth, len(candidates) - 1)})"
        return ToolResult(
            success=True,
            output=(
                f"Arrow shown on {label!r}"
                + (f" ({node.role})" if node.role else "")
                + (others if len(candidates) > 1 else "")
                + ". Nothing was clicked."
            ),
        )
