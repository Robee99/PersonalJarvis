"""REST API for the World screen's incidents (``jarvis.intel``).

    GET /api/intel/events?rev=N   → every current incident, newest first, with
                                    each source's status. When ``rev`` equals
                                    the store's revision the body is just
                                    ``{"rev": N, "unchanged": true}``.
    GET /api/intel/events/{id}    → one incident with its related reports and
                                    its source's status; 404 when it is gone.
    GET /api/intel/sources        → the sources, their terms and their status.
    GET /api/intel/live           → the live news channels and city cameras
                                    (``jarvis.intel.live``); static, no fetch.
    GET /api/intel/live/now/{key} → the video a channel's live page points at
                                    now (``live.now_playing``), or null.
    GET /api/intel/cctv           → the public cameras God's Eye View lists
                                    (``jarvis.intel.cctv``), by city.
    GET /api/intel/cctv/health    → how many cameras GEV last served live.

A request starts a refresh of the sources that are due. With nothing loaded
yet it waits for the first answers (up to a few seconds); otherwise it answers
from the store at once and the refresh lands on the next poll. Every source is
free and keyless, and no request carries anything about the user.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from jarvis.intel import cctv, live, query
from jarvis.intel.service import shared_service

router = APIRouter(prefix="/api/intel", tags=["intel"])

#: How long the first request waits for the first answers.
FIRST_WAIT_S = 8.0
#: How long a later request lets a running refresh finish before answering.
LATER_WAIT_S = 0.05
ID_MAX = 200


async def _fresh() -> Any:
    service = shared_service()
    await service.refresh(FIRST_WAIT_S if len(service.store) == 0 else LATER_WAIT_S)
    return service


@router.get("/events")
async def intel_events(rev: int | None = None) -> dict[str, Any]:
    service = await _fresh()
    store = service.store
    if rev is not None and rev == store.rev and store.rev > 0:
        return {"rev": store.rev, "unchanged": True}
    return {
        "rev": store.rev,
        "unchanged": False,
        "events": [service.record(event) for event in store.events()],
        "sources": service.sources_json(),
        "regions": query.regions_json(),
    }


@router.get("/events/{event_id:path}")
async def intel_event(event_id: str) -> dict[str, Any]:
    if len(event_id) > ID_MAX:
        raise HTTPException(status_code=404, detail="no such incident")
    service = await _fresh()
    event = service.store.get(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="no such incident")
    related = [service.store.get(rid) for rid in service.store.related(event_id)]
    source = next(s for s in service.sources_json() if s["id"] == event.source)
    return {
        "event": service.record(event),
        "related": [service.record(r) for r in related if r is not None],
        "source": source,
    }


@router.get("/sources")
async def intel_sources() -> dict[str, Any]:
    service = await _fresh()
    return {"sources": service.sources_json(), "regions": query.regions_json()}


@router.get("/live")
def intel_live() -> dict[str, Any]:
    return live.catalog_json()


@router.get("/live/now/{key}")
async def intel_live_now(key: str) -> dict[str, Any]:
    if len(key) > ID_MAX or live.by_key(key) is None:
        raise HTTPException(status_code=404, detail="no such stream")
    return {"key": key, "video": await live.now_playing(key)}


@router.get("/cctv")
async def intel_cctv() -> dict[str, Any]:
    return await cctv.catalog()


@router.get("/cctv/health")
async def intel_cctv_health() -> dict[str, int]:
    return await cctv.health()
