"""Hermes Agent capability inventory for the Tool Armory.

``GET /api/hermes/inventory`` → what Hermes Agent can use on this computer,
discovered at runtime (see :mod:`jarvis.plugins.brain.hermes_inventory`):
skills by provenance, configured MCP servers, plugins and toolsets. Each part
carries its own error when it could not be read; ``available`` is ``false``
with a ``reason`` when no Hermes home exists. Never includes secrets.

``GET /api/hermes/catalog`` → the MCP servers and optional skills Hermes can
add, with descriptions and whether each is installed.

``POST /api/hermes/action`` → enable or disable a skill, MCP server, plugin or
toolset, or install a catalog entry, through Hermes's own CLI (see
:mod:`jarvis.plugins.brain.hermes_actions`).

Plain ``def``: these walk folders, spawn ``hermes`` and call the local Hermes
API server, so they run in the threadpool, not on the loop.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from jarvis.plugins.brain.hermes_actions import ActionRefused, collect_catalog, run_action
from jarvis.plugins.brain.hermes_inventory import (
    NOT_FOUND_REASON,
    InventoryPartError,
    clear_cache,
    collect_hermes_inventory,
    find_hermes_home,
)

router = APIRouter(prefix="/api/hermes", tags=["hermes"])


@router.get("/inventory")
def hermes_inventory(refresh: bool = False) -> dict[str, Any]:
    """Hermes Agent's skills, MCP servers, plugins and toolsets (cached ~30 s).

    ``refresh=true`` skips the cache and reads everything again.
    """
    if refresh:
        clear_cache()
    return collect_hermes_inventory()


@router.get("/catalog")
def hermes_catalog() -> dict[str, Any]:
    """MCP servers and optional skills Hermes can add, each marked installed or not."""
    home = find_hermes_home()
    if home is None:
        return {"available": False, "error": NOT_FOUND_REASON, "mcp_servers": [], "skills": []}
    return collect_catalog(home)


class HermesAction(BaseModel):
    kind: str = Field(..., max_length=32)
    op: str = Field(..., max_length=16)
    name: str = Field(..., max_length=160)


@router.post("/action", openapi_extra={"x-jarvis-dangerous": True})
def hermes_action(body: HermesAction) -> dict[str, Any]:
    """Change what Hermes can use: one ``hermes`` command, answered with its outcome."""
    home = find_hermes_home()
    if home is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_REASON)
    try:
        return run_action(body.kind, body.op, body.name, home)
    except ActionRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InventoryPartError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
