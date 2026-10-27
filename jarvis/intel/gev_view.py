"""Fly the World screen's globe where God's Eye View's own tool pointed.

GEV's MCP tool ``show_in_gods_eye_view`` geocodes a place and answers with a
view (camera, layers) and a URL; it does not move any window. Live 2026-10-26
"take me to Mumbai in God's Eye View" called it three times, each a success,
and the World screen never moved: the chat panel's bridge
(frontend lib/gevViewBridge.ts) only runs where a chat trace is drawn, and
reads one JSON object, while Jarvis's MCP adapter hands over a list of texts.
Here the adapter's raw result becomes a ``WorldIntelCommand(action="view")``
on the bus, whatever surface asked.
"""

from __future__ import annotations

import json
from typing import Any

from jarvis.intel.mcp_tools import Publisher, publish_to_ui

#: GEV's MCP tool that answers with a view.
GEV_VIEW_TOOL = "show_in_gods_eye_view"
#: A view larger than this is not a camera request.
_MAX_VIEW_CHARS = 100_000


def _view_in(value: Any, depth: int = 0) -> dict[str, Any] | None:
    if depth > 4:
        return None
    if isinstance(value, str):
        if len(value) > _MAX_VIEW_CHARS or not value.lstrip().startswith(("{", "[")):
            return None
        try:
            return _view_in(json.loads(value), depth + 1)
        except ValueError:  # Malformed nested JSON cannot provide a usable view description.
            return None
    if isinstance(value, list):
        for item in value:
            found = _view_in(item, depth + 1)
            if found is not None:
                return found
        return None
    if not isinstance(value, dict):
        return None
    view = value.get("view")
    if isinstance(view, dict) and isinstance(view.get("camera"), dict):
        camera = view["camera"]
        lat, lon = camera.get("lat"), camera.get("lon")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return view
        return None
    for key in ("structuredContent", "structured_content", "content", "text", "result", "data"):
        if key in value:
            found = _view_in(value[key], depth + 1)
            if found is not None:
                return found
    return None


def gev_view_from_result(raw: Any) -> dict[str, Any] | None:
    """The camera view in a ``show_in_gods_eye_view`` result, else None.

    Takes the raw MCP ``CallToolResult`` (its ``structuredContent`` or text
    items) or an already normalized string, list or dict.
    """
    structured = getattr(raw, "structuredContent", None)
    if structured is not None:
        found = _view_in(structured)
        if found is not None:
            return found
    content = getattr(raw, "content", None)
    if content is not None:
        return _view_in([getattr(item, "text", None) for item in content])
    return _view_in(raw)


async def show_gev_view(view: dict[str, Any], publish: Publisher = publish_to_ui) -> bool:
    """Open the World screen and fly its globe to ``view``; False with no window."""
    from jarvis.core.events import NavigateSidebar, WorldIntelCommand

    return await publish(
        [
            NavigateSidebar(section="world-intelligence", source_layer="intel.gev_view"),
            WorldIntelCommand(
                action="view",
                view=json.dumps(view, separators=(",", ":")),
                source_layer="intel.gev_view",
            ),
        ]
    )


__all__ = ["GEV_VIEW_TOOL", "gev_view_from_result", "show_gev_view"]
