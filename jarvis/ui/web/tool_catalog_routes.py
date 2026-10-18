"""The Tool Catalog: every MCP server, skill, plugin, app, CLI and toolset once.

``GET /api/tool-catalog?q=&type=`` → one record per canonical upstream (see
:mod:`jarvis.tool_catalog`), deduplicated across Jarvis and Hermes Agent, each
with its runtime bindings and the states ``available``, ``installed``,
``enabled``, ``connected``, ``visible`` (to the active brain) and ``verified``
(only from a real tool call in the verification file). ``counts`` counts the
types among the records that match ``q`` (before the ``type`` filter), so the
UI's filter chips can show them. Read-only; never includes a secret.

Plain ``def``: it reads mcp.json, the keyring, Hermes's files and may run
``hermes plugins list``, so it runs in the threadpool, not on the loop.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from jarvis.tool_catalog.build import TYPE_ORDER, filter_items, type_counts
from jarvis.tool_catalog.sources import collect_catalog

router = APIRouter(prefix="/api/tool-catalog", tags=["tool-catalog"])


@router.get("", openapi_extra={"x-jarvis-readonly": True})
def tool_catalog(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    type: str | None = Query(default=None, max_length=16),  # noqa: A002 — the public query name
) -> dict[str, Any]:
    """Canonical tool records, optionally filtered by search words and one type tag."""
    catalog = collect_catalog(request.app.state)
    matching = filter_items(catalog["items"], q=q)
    items = filter_items(matching, type_=type)
    return {
        **catalog,
        "items": items,
        "total": len(items),
        "total_unfiltered": catalog["total"],
        "counts": type_counts(matching),
        "types": list(TYPE_ORDER),
        "query": {"q": q or "", "type": type or ""},
    }
