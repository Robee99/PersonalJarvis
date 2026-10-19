"""Public traffic and city cameras, as God's Eye View lists them.

God's Eye View (GEV) keeps a catalog of public cameras run by road and city
authorities (about 3,000: TxDOT, Caltrans, Transport for London, DriveBC and
others) and serves each one's latest frame at ``/api/cctv/frame/<id>``. JARVIS
does not keep a second catalog: it reads GEV's list from the local GEV server,
keeps the fields a camera wall needs, and the page loads each frame straight
from GEV. When GEV is not running the list is empty and says why.

Only a loopback GEV server is read, so a changed setting cannot point JARVIS
at a host elsewhere.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any
from urllib.parse import urlsplit

from jarvis.core.http_pool import HttpClientPool

log = logging.getLogger(__name__)

DEFAULT_GEV_URL = "http://127.0.0.1:4173"
#: The list changes when GEV adds a camera pack, not minute to minute.
SOURCES_TTL_S = 10 * 60
#: After GEV failed to answer, ask again this soon.
RETRY_S = 30.0
HEALTH_TTL_S = 30.0
SOURCES_LIMIT = 30_000_000
_LOOPBACK = {"127.0.0.1", "localhost", "[::1]", "::1"}

_POOL = HttpClientPool(timeout_s=10.0, client_kwargs={"follow_redirects": False})
_sources: tuple[float, dict[str, Any]] | None = None
_health: tuple[float, dict[str, int]] | None = None


def gev_base() -> str | None:
    """The GEV server's origin, or ``None`` when the setting is not loopback."""
    raw = (os.environ.get("JARVIS_GEV_URL") or DEFAULT_GEV_URL).strip()
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or parts.hostname is None:
        return None
    if parts.hostname not in _LOOPBACK or parts.username or parts.password:
        return None
    return f"{parts.scheme}://{parts.netloc}"


async def _fetch_json(url: str, limit: int) -> Any:
    async def fetch() -> Any:
        async with _POOL.client().stream("GET", url) as res:
            res.raise_for_status()
            body = bytearray()
            async for chunk in res.aiter_bytes():
                body.extend(chunk)
                if len(body) > limit:
                    raise ValueError(f"{url} sent more than {limit} bytes")
        return json.loads(bytes(body))

    return await asyncio.wait_for(fetch(), timeout=20.0)


async def fetch_json(url: str, limit: int) -> Any:
    """Every GEV read goes through this name; tests replace it."""
    return await _fetch_json(url, limit)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def slim(raw: Any) -> dict[str, Any] | None:
    """One camera with the fields the wall shows, or ``None`` if unplaceable."""
    if not isinstance(raw, dict):
        return None
    cam_id = str(raw.get("id") or "").strip()
    lat, lon = _number(raw.get("lat")), _number(raw.get("lon"))
    if not cam_id or lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    feed = str(raw.get("feedType") or "image").lower()
    city = str(raw.get("city") or "").strip()
    return {
        "id": cam_id[:120],
        "name": str(raw.get("name") or cam_id).strip()[:160],
        "city": city[:80],
        "city_id": str(raw.get("cityId") or city.lower() or "other")[:80],
        "provider": str(raw.get("provider") or "").strip()[:120],
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "feed": "video" if feed in {"hls", "mp4", "video", "mjpeg", "dash"} else "image",
        "heading": _number(raw.get("headingDeg")),
    }


def cities(cameras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cameras grouped by city, largest first, with a centre point."""
    groups: dict[str, dict[str, Any]] = {}
    for cam in cameras:
        group = groups.setdefault(
            cam["city_id"],
            {
                "id": cam["city_id"],
                "name": cam["city"] or cam["city_id"],
                "count": 0,
                "video": 0,
                "lat": 0.0,
                "lon": 0.0,
                "providers": set(),
            },
        )
        group["count"] += 1
        group["video"] += cam["feed"] == "video"
        group["lat"] += cam["lat"]
        group["lon"] += cam["lon"]
        if cam["provider"]:
            group["providers"].add(cam["provider"])
    out = []
    for group in groups.values():
        n = group["count"]
        out.append(
            {
                **group,
                "lat": round(group["lat"] / n, 4),
                "lon": round(group["lon"] / n, 4),
                "providers": sorted(group["providers"]),
            }
        )
    out.sort(key=lambda g: (-g["count"], g["name"]))
    return out


async def catalog(*, now: float | None = None) -> dict[str, Any]:
    """Every camera GEV lists, its cities, and whether GEV answered."""
    global _sources
    clock = time.monotonic() if now is None else now
    if _sources is not None and _sources[0] > clock:
        return _sources[1]
    base = gev_base()
    if base is None:
        body = _empty("not_loopback", None)
        _sources = (clock + RETRY_S, body)
        return body
    try:
        raw = await fetch_json(f"{base}/api/cctv/sources", SOURCES_LIMIT)
    except Exception as exc:  # noqa: BLE001 - GEV not running is the usual case, not a fault
        log.info("cctv: God's Eye View did not list its cameras: %s", exc)
        body = _empty("gev_down", base)
        _sources = (clock + RETRY_S, body)
        return body
    listed = raw.get("sources") if isinstance(raw, dict) else raw
    cameras = [cam for cam in (slim(item) for item in listed or []) if cam is not None]
    body = {
        "available": True,
        "reason": None,
        "gev": base,
        "cameras": cameras,
        "cities": cities(cameras),
        "fetched_at": int(time.time() * 1000),
    }
    _sources = (clock + SOURCES_TTL_S, body)
    return body


def _empty(reason: str, base: str | None) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "gev": base,
        "cameras": [],
        "cities": [],
        "fetched_at": int(time.time() * 1000),
    }


async def health(*, now: float | None = None) -> dict[str, int]:
    """How many cameras GEV last served live, degraded or not at all.

    GEV records a camera's state when its frame is fetched, so this counts
    the cameras someone has looked at, not the whole catalog.
    """
    global _health
    clock = time.monotonic() if now is None else now
    if _health is not None and _health[0] > clock:
        return _health[1]
    counts = {"ok": 0, "degraded": 0, "error": 0, "unknown": 0}
    base = gev_base()
    if base is not None:
        try:
            raw = await fetch_json(f"{base}/api/cctv/health", 5_000_000)
        except Exception as exc:  # noqa: BLE001 - an unreachable GEV leaves every count at zero
            log.info("cctv: God's Eye View health unavailable: %s", exc)
        else:
            entries = raw.get("cameras") if isinstance(raw, dict) else raw
            for entry in entries if isinstance(entries, list) else []:
                status = (
                    str(entry.get("status") or "unknown") if isinstance(entry, dict) else "unknown"
                )
                counts[status if status in counts else "unknown"] += 1
    _health = (clock + HEALTH_TTL_S, counts)
    return counts


def reset() -> None:
    """Forget the cached answers. Tests only."""
    global _sources, _health
    _sources = None
    _health = None
