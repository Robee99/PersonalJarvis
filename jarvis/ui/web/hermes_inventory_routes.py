"""Hermes Agent capability inventory for the Tool Armory (read-only).

``GET /api/hermes/inventory`` → what Hermes Agent can use on this computer,
discovered at runtime (see :mod:`jarvis.plugins.brain.hermes_inventory`):
skills by provenance, configured MCP servers, plugins and toolsets. Each part
carries its own error when it could not be read; ``available`` is ``false``
with a ``reason`` when no Hermes home exists. Never includes secrets.

Plain ``def``: the scan walks folders, spawns ``hermes plugins list`` and calls
the local Hermes API server, so it runs in the threadpool, not on the loop.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from jarvis.plugins.brain.hermes_inventory import clear_cache, collect_hermes_inventory

router = APIRouter(prefix="/api/hermes", tags=["hermes"])


@router.get("/inventory")
def hermes_inventory(refresh: bool = False) -> dict[str, Any]:
    """Hermes Agent's skills, MCP servers, plugins and toolsets (cached ~30 s).

    ``refresh=true`` skips the cache and reads everything again.
    """
    if refresh:
        clear_cache()
    return collect_hermes_inventory()
