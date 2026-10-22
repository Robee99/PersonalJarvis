"""REST API for the HUD's public-data panels — free, keyless sources only.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET /api/public/quakes    → USGS earthquakes of magnitude 2.5+ from the
                                last day, newest first.
    GET /api/public/events    → open natural events (wildfires, storms,
                                volcanoes, ...) from NASA EONET.
    GET /api/public/space     → planetary Kp index and F10.7 solar flux from
                                NOAA SWPC, plus close asteroid approaches from
                                NASA/JPL CNEOS.
    GET /api/public/iss       → where the International Space Station is now.
    GET /api/public/stations  → orbital elements of the crewed stations (and
                                their companions) from CelesTrak.

Every source is public, free and needs no key; no request carries anything
about the user. Each route keeps its last good answer in memory and serves it
until its TTL runs out. A refresh that fails serves that last answer with
``stale: true``; with nothing cached the route still answers 200, with
``available: false`` and empty data, so the HUD can show an honest error state
instead of a broken panel. A number a source did not give is ``null``, never 0.

Every body carries ``available``, ``stale``, ``fetched_at`` (unix seconds of
the data's fetch, ``null`` when there is none), ``source`` and ``source_url``
(the attribution page).

Upstream etiquette: each route has its own lock, so concurrent requests share
one upstream call, and no upstream is called more often than its minimum
interval whatever the clients do (the ISS API asks for at most one request per
second; this route asks at most once per five seconds).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from jarvis.core.http_pool import HttpClientPool

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/public", tags=["public-data"])

USER_AGENT = "Jarvis-HUD/1.0"
#: Per-phase httpx timeout (connect / read / ...), and the ceiling for a whole
#: fetch so a server that drips bytes cannot hold a lock for long.
FETCH_TIMEOUT_S = 6.0
FETCH_DEADLINE_S = 10.0
MAX_RESPONSE_BYTES = 3_000_000
#: After a failed refresh, wait this long before asking that upstream again.
FAILURE_RETRY_S = 60.0

QUAKES_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
EVENTS_URL = "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=60"
KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
F107_URL = "https://services.swpc.noaa.gov/json/f107_cm_flux.json"
CAD_URL = "https://ssd-api.jpl.nasa.gov/cad.api?dist-max=0.05&date-min=now&date-max=%2B60&sort=dist"
ISS_URL = "https://api.wheretheiss.at/v1/satellites/25544"
STATIONS_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=json"

AU_KM = 149_597_870.7
LUNAR_DISTANCE_KM = 384_400.0
EARTH_MU_KM3_S2 = 398_600.4418
EARTH_RADIUS_KM = 6_378.137

QUAKES_MAX = 200
EVENTS_MAX = 60
KP_SERIES_LEN = 24
NEOS_MAX = 12
STATIONS_MAX = 60


class UpstreamError(RuntimeError):
    """An upstream answered, but not with something this module can use."""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

#: One warm client for every poll here (AP-33): the HUD polls these routes on
#: timers, and a fresh client per refresh would leave a TIME_WAIT socket behind
#: each one. Tests swap this for a pool over ``httpx.MockTransport``.
_POOL = HttpClientPool(
    timeout_s=FETCH_TIMEOUT_S,
    client_kwargs={
        "follow_redirects": True,
        "headers": {"User-Agent": USER_AGENT, "Accept": "application/json"},
    },
)


async def _fetch_json(url: str) -> Any:
    """GET ``url`` and decode its JSON, refusing a body over the size cap."""

    async def fetch() -> Any:
        async with _POOL.client().stream("GET", url) as res:
            res.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            async for chunk in res.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise UpstreamError(f"{url} sent more than {MAX_RESPONSE_BYTES} bytes")
                chunks.append(chunk)
        return json.loads(b"".join(chunks))

    return await asyncio.wait_for(fetch(), timeout=FETCH_DEADLINE_S)


async def fetch_json(url: str) -> Any:
    """The fetch every route uses; tests monkeypatch this name."""
    return await _fetch_json(url)


# ---------------------------------------------------------------------------
# Defensive coercion
# ---------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    """A finite float, or ``None`` for anything else (bools included)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):  # not a number: unknown, never invented
        return None
    return number if math.isfinite(number) else None


def _int(value: Any) -> int | None:
    number = _num(value)
    return int(number) if number is not None and number == int(number) else None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _url(value: Any) -> str | None:
    text = _text(value)
    return text if text and text.startswith(("https://", "http://")) else None


def _lon_lat(lon: Any, lat: Any) -> tuple[float, float] | None:
    x, y = _num(lon), _num(lat)
    if x is None or y is None or not (-180.0 <= x <= 180.0) or not (-90.0 <= y <= 90.0):
        return None
    return x, y


def _utc_ms(value: Any) -> int | None:
    """An ISO-8601 time in ms since the epoch; a time without a zone is UTC.

    Covers ``2026-10-18T12:00:00``, ``2026-10-18 12:00:00.000`` and ``...Z``.
    """
    text = _text(value)
    if text is None:
        return None
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:  # an unparseable time is unknown; the caller skips the row
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return int(stamp.timestamp() * 1000)


_MONTHS = {
    name: index
    for index, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}


def _cad_ms(value: Any) -> int | None:
    """JPL's ``2026-Oct-21 14:32`` (UTC) in ms.

    Parsed by hand: ``strptime("%b")`` follows the process locale, and a
    German Windows would read "Oct" as no month at all.
    """
    text = _text(value)
    if text is None:
        return None
    try:
        date_part, _, time_part = text.partition(" ")
        year, month_name, day = date_part.split("-")
        hour, minute = (time_part or "00:00").split(":")[:2]
        month = _MONTHS[month_name.lower()]
        stamp = datetime(int(year), month, int(day), int(hour), int(minute), tzinfo=UTC)
    except (KeyError, ValueError):  # malformed date: the caller skips the row
        return None
    return int(stamp.timestamp() * 1000)


# ---------------------------------------------------------------------------
# Parsers (pure: decoded JSON in, API rows out; a malformed row is skipped)
# ---------------------------------------------------------------------------


def parse_quakes(data: Any) -> list[dict[str, Any]]:
    """USGS GeoJSON features → quake rows, newest first."""
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        raise UpstreamError("USGS answer has no features list")
    items: list[dict[str, Any]] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(props, dict) or not isinstance(geometry, dict):
            continue
        coords = geometry.get("coordinates")
        quake_id = _text(feature.get("id"))
        time_ms = _int(props.get("time"))
        if not isinstance(coords, list) or len(coords) < 2 or not quake_id or time_ms is None:
            continue
        position = _lon_lat(coords[0], coords[1])
        if position is None:
            continue
        tsunami = props.get("tsunami")
        items.append(
            {
                "id": quake_id,
                "mag": _num(props.get("mag")),
                "place": _text(props.get("place")),
                "time_ms": time_ms,
                "lon": position[0],
                "lat": position[1],
                "depth_km": _num(coords[2]) if len(coords) > 2 else None,
                "url": _url(props.get("url")),
                "alert": _text(props.get("alert")),
                "tsunami": bool(tsunami) if tsunami in (0, 1) else None,
                "sig": _int(props.get("sig")),
            }
        )
    items.sort(key=lambda q: q["time_ms"], reverse=True)
    return items[:QUAKES_MAX]


def _ring_centroid(ring: Any) -> tuple[float, float] | None:
    """Mean of a polygon ring's vertices (closing vertex counted once).

    A ring that straddles the antimeridian is averaged on a 0..360 longitude
    span so a fire on Fiji's 180th meridian does not land in Africa.
    """
    if not isinstance(ring, list):
        return None
    points = (_lon_lat(v[0], v[1]) for v in ring if isinstance(v, list) and len(v) >= 2)
    vertices = [p for p in points if p is not None]
    if len(vertices) > 1 and vertices[0] == vertices[-1]:
        vertices = vertices[:-1]
    if not vertices:
        return None
    lons = [lon for lon, _ in vertices]
    if max(lons) - min(lons) > 180.0:
        lons = [lon + 360.0 if lon < 0 else lon for lon in lons]
    lon = sum(lons) / len(lons)
    if lon > 180.0:
        lon -= 360.0
    lat = sum(lat for _, lat in vertices) / len(vertices)
    return lon, lat


def _geometry_position(geometry: dict[str, Any]) -> tuple[float, float] | None:
    coords = geometry.get("coordinates")
    kind = geometry.get("type")
    if kind == "Point" and isinstance(coords, list) and len(coords) >= 2:
        return _lon_lat(coords[0], coords[1])
    if kind == "Polygon" and isinstance(coords, list) and coords:
        return _ring_centroid(coords[0])
    return None


def parse_events(data: Any) -> list[dict[str, Any]]:
    """EONET events → one row per event at its newest geometry, newest first."""
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        raise UpstreamError("EONET answer has no events list")
    items: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id, title = _text(event.get("id")), _text(event.get("title"))
        geometries = event.get("geometry")
        if not event_id or not title or not isinstance(geometries, list):
            continue
        dated = [
            (stamp, g)
            for g in geometries
            if isinstance(g, dict) and (stamp := _utc_ms(g.get("date"))) is not None
        ]
        if not dated:
            continue
        date_ms, newest = max(dated, key=lambda pair: pair[0])
        position = _geometry_position(newest)
        if position is None:
            continue
        categories = event.get("categories")
        first = categories[0] if isinstance(categories, list) and categories else None
        category = first if isinstance(first, dict) else {}
        items.append(
            {
                "id": event_id,
                "title": title,
                "category": _text(category.get("title")),
                "category_id": _text(category.get("id")),
                "lon": position[0],
                "lat": position[1],
                "date_ms": date_ms,
                "link": _url(event.get("link")),
                "magnitude": _num(newest.get("magnitudeValue")),
                "unit": _text(newest.get("magnitudeUnit")),
            }
        )
    items.sort(key=lambda e: e["date_ms"], reverse=True)
    return items[:EVENTS_MAX]


def _rows_as_dicts(data: Any) -> list[dict[str, Any]]:
    """SWPC's two shapes → dicts: objects already, or rows under a header row."""
    if not isinstance(data, list):
        raise UpstreamError("SWPC answer is not a list")
    if data and isinstance(data[0], list):
        header = [str(name) for name in data[0]]
        return [dict(zip(header, row, strict=False)) for row in data[1:] if isinstance(row, list)]
    return [row for row in data if isinstance(row, dict)]


def parse_kp(data: Any) -> dict[str, Any] | None:
    """Planetary Kp readings → the newest value and the last 24, oldest first."""
    readings: list[tuple[int, float]] = []
    for row in _rows_as_dicts(data):
        stamp, kp = _utc_ms(row.get("time_tag")), _num(row.get("Kp"))
        if stamp is not None and kp is not None and 0.0 <= kp <= 9.0:
            readings.append((stamp, kp))
    if not readings:
        return None
    readings.sort()
    latest_ms, latest = readings[-1]
    return {
        "value": latest,
        "time_ms": latest_ms,
        "series": [{"time_ms": t, "kp": kp} for t, kp in readings[-KP_SERIES_LEN:]],
    }


def parse_f107(data: Any) -> dict[str, Any] | None:
    """The newest F10.7 (2800 MHz) flux reading with a numeric value."""
    best: tuple[int, float] | None = None
    for row in _rows_as_dicts(data):
        frequency = _num(row.get("frequency"))
        if frequency is not None and frequency != 2800:
            continue
        stamp, flux = _utc_ms(row.get("time_tag")), _num(row.get("flux"))
        if stamp is None or flux is None or flux <= 0:
            continue
        if best is None or stamp > best[0]:
            best = (stamp, flux)
    return None if best is None else {"value": best[1], "time_ms": best[0]}


def parse_close_approaches(data: Any) -> list[dict[str, Any]]:
    """JPL CAD rows → close approaches, soonest first."""
    if not isinstance(data, dict):
        raise UpstreamError("JPL CAD answer is not an object")
    fields, rows = data.get("fields"), data.get("data")
    if not isinstance(fields, list):
        raise UpstreamError("JPL CAD answer has no fields")
    if rows is None:  # CAD omits "data" when nothing passes close enough
        return []
    if not isinstance(rows, list):
        raise UpstreamError("JPL CAD data is not a list")
    names = [str(name) for name in fields]
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        record = dict(zip(names, row, strict=False))
        des, time_ms = _text(record.get("des")), _cad_ms(record.get("cd"))
        dist_au = _num(record.get("dist"))
        if not des or time_ms is None or dist_au is None or dist_au < 0:
            continue
        dist_km = dist_au * AU_KM
        v_kms = _num(record.get("v_rel"))
        items.append(
            {
                "des": des,
                "time_ms": time_ms,
                "dist_au": dist_au,
                "dist_km": round(dist_km),
                "dist_ld": round(dist_km / LUNAR_DISTANCE_KM, 2),
                "v_kms": round(v_kms, 2) if v_kms is not None else None,
                "h": _num(record.get("h")),
            }
        )
    items.sort(key=lambda n: n["time_ms"])
    return items[:NEOS_MAX]


def parse_iss(data: Any) -> dict[str, Any]:
    """wheretheiss.at → position; a reading without a position is a failure."""
    if not isinstance(data, dict):
        raise UpstreamError("ISS answer is not an object")
    position = _lon_lat(data.get("longitude"), data.get("latitude"))
    if position is None:
        raise UpstreamError("ISS answer has no usable position")
    seconds = _num(data.get("timestamp"))
    return {
        "lat": position[1],
        "lon": position[0],
        "alt_km": _num(data.get("altitude")),
        "vel_kmh": _num(data.get("velocity")),
        "visibility": _text(data.get("visibility")),
        "time_ms": int(seconds * 1000) if seconds is not None else None,
    }


def orbit_from_elements(mean_motion: float, eccentricity: float) -> dict[str, float]:
    """Period, apogee and perigee height from mean motion (rev/day) and e."""
    n_rad_s = mean_motion * 2.0 * math.pi / 86_400.0
    semi_major_km = (EARTH_MU_KM3_S2 / n_rad_s**2) ** (1.0 / 3.0)
    return {
        "period_min": round(1440.0 / mean_motion, 2),
        "apogee_km": round(semi_major_km * (1 + eccentricity) - EARTH_RADIUS_KM, 1),
        "perigee_km": round(semi_major_km * (1 - eccentricity) - EARTH_RADIUS_KM, 1),
    }


def parse_stations(data: Any) -> list[dict[str, Any]]:
    """CelesTrak GP (OMM JSON) elements → station rows."""
    if not isinstance(data, list):
        raise UpstreamError("CelesTrak answer is not a list")
    items: list[dict[str, Any]] = []
    for element in data:
        if not isinstance(element, dict):
            continue
        name = _text(element.get("OBJECT_NAME"))
        norad_id = _int(element.get("NORAD_CAT_ID"))
        mean_motion = _num(element.get("MEAN_MOTION"))
        eccentricity = _num(element.get("ECCENTRICITY"))
        if not name or norad_id is None or mean_motion is None or mean_motion <= 0:
            continue
        if eccentricity is None or not (0.0 <= eccentricity < 1.0):
            continue
        items.append(
            {
                "name": name,
                "norad_id": norad_id,
                "epoch_ms": _utc_ms(element.get("EPOCH")),
                "mean_motion": mean_motion,
                "eccentricity": eccentricity,
                "inclination": _num(element.get("INCLINATION")),
                "raan": _num(element.get("RA_OF_ASC_NODE")),
                "arg_perigee": _num(element.get("ARG_OF_PERICENTER")),
                "mean_anomaly": _num(element.get("MEAN_ANOMALY")),
                **orbit_from_elements(mean_motion, eccentricity),
            }
        )
        if len(items) >= STATIONS_MAX:
            break
    return items


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


@dataclass
class _Feed:
    """One route's cache: the last good body, when it expires, and its lock."""

    source: str
    source_url: str
    ttl_s: float
    empty: Callable[[], dict[str, Any]]
    #: Never call the upstream more often than this, success or failure.
    min_interval_s: float = FAILURE_RETRY_S
    body: dict[str, Any] | None = None
    fetched_at: float | None = None
    expires_at: float = 0.0
    attempted_at: float | None = None
    stale: bool = False
    _lock: asyncio.Lock | None = field(default=None, repr=False)
    _lock_loop: Any = field(default=None, repr=False)

    def lock(self) -> asyncio.Lock:
        """This feed's lock for the running loop (a TestClient runs several)."""
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

    def reset(self) -> None:
        self.body, self.fetched_at, self.attempted_at = None, None, None
        self.expires_at, self.stale = 0.0, False
        self._lock, self._lock_loop = None, None

    def answer(self) -> dict[str, Any]:
        meta = {"source": self.source, "source_url": self.source_url}
        if self.body is None:
            return {
                **self.empty(),
                "available": False,
                "stale": False,
                "fetched_at": None,
                **meta,
            }
        return {
            **self.body,
            "available": True,
            "stale": self.stale,
            "fetched_at": self.fetched_at,
            **meta,
        }


#: A refresh returns the body and whether it is complete; an incomplete one
#: (a source of several failed) is kept only until the failure retry.
Refresh = Callable[[], Awaitable[tuple[dict[str, Any], bool]]]


def _monotonic() -> float:
    """The cache's clock; tests replace this name, never ``time.monotonic``
    itself (the event loop runs on that one)."""
    return time.monotonic()


async def _serve(feed: _Feed, refresh: Refresh) -> dict[str, Any]:
    if feed.body is not None and _monotonic() < feed.expires_at:
        return feed.answer()
    async with feed.lock():
        now = _monotonic()
        # Another request may have refreshed while this one waited.
        if feed.body is not None and now < feed.expires_at:
            return feed.answer()
        if feed.attempted_at is not None and now - feed.attempted_at < feed.min_interval_s:
            return feed.answer()
        feed.attempted_at = now
        import httpx  # noqa: PLC0415 - lazy (AP-26)

        try:
            body, complete = await refresh()
        except (httpx.HTTPError, UpstreamError, ValueError, TypeError, TimeoutError) as exc:
            log.info(
                "public data: %s refresh failed (%s: %s); serving %s",
                feed.source,
                type(exc).__name__,
                exc,
                "the last good answer" if feed.body is not None else "nothing",
            )
            feed.stale = feed.body is not None
            return feed.answer()
        feed.body, feed.fetched_at, feed.stale = body, time.time(), False
        feed.expires_at = now + (feed.ttl_s if complete else min(feed.ttl_s, FAILURE_RETRY_S))
        return feed.answer()


_QUAKES = _Feed(
    source="USGS Earthquake Hazards Program",
    source_url="https://earthquake.usgs.gov/earthquakes/feed/",
    ttl_s=300.0,
    empty=lambda: {"items": []},
)
_EVENTS = _Feed(
    source="NASA EONET",
    source_url="https://eonet.gsfc.nasa.gov/",
    ttl_s=900.0,
    empty=lambda: {"items": []},
)
_SPACE_PARTS = ("kp", "f107", "neos")
_SPACE = _Feed(
    source="NOAA SWPC · NASA/JPL CNEOS",
    source_url="https://www.swpc.noaa.gov/",
    ttl_s=900.0,
    empty=lambda: {"kp": None, "f107": None, "neos": [], "failed": list(_SPACE_PARTS)},
)
ISS_MIN_INTERVAL_S = 5.0
_ISS = _Feed(
    source="Where the ISS at?",
    source_url="https://wheretheiss.at/",
    ttl_s=ISS_MIN_INTERVAL_S,
    min_interval_s=ISS_MIN_INTERVAL_S,
    empty=lambda: {
        "lat": None,
        "lon": None,
        "alt_km": None,
        "vel_kmh": None,
        "visibility": None,
        "time_ms": None,
    },
)
_STATIONS = _Feed(
    source="CelesTrak",
    source_url="https://celestrak.org/",
    ttl_s=7200.0,
    empty=lambda: {"items": []},
)
_FEEDS = (_QUAKES, _EVENTS, _SPACE, _ISS, _STATIONS)


def reset_caches() -> None:
    """Forget every cached answer and the pooled client. Tests only."""
    for feed in _FEEDS:
        feed.reset()
    _POOL.forget()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/quakes", summary="Earthquakes M2.5+ of the last day (USGS)")
async def quakes() -> dict[str, Any]:
    """``items``: ``{id, mag, place, time_ms, lon, lat, depth_km, url, alert,
    tsunami, sig}``, newest first."""

    async def refresh() -> tuple[dict[str, Any], bool]:
        return {"items": parse_quakes(await fetch_json(QUAKES_URL))}, True

    return await _serve(_QUAKES, refresh)


@router.get("/events", summary="Open natural events (NASA EONET)")
async def events() -> dict[str, Any]:
    """``items``: ``{id, title, category, category_id, lon, lat, date_ms, link,
    magnitude, unit}`` at each event's newest position, newest first."""

    async def refresh() -> tuple[dict[str, Any], bool]:
        return {"items": parse_events(await fetch_json(EVENTS_URL))}, True

    return await _serve(_EVENTS, refresh)


@router.get("/space", summary="Kp index, F10.7 flux and close asteroid approaches")
async def space() -> dict[str, Any]:
    """``kp``, ``f107`` and ``neos``; a source that failed is named in
    ``failed`` and its part is ``null`` (or ``[]`` for ``neos``)."""

    async def refresh() -> tuple[dict[str, Any], bool]:
        parsers: dict[str, tuple[str, Callable[[Any], Any]]] = {
            "kp": (KP_URL, parse_kp),
            "f107": (F107_URL, parse_f107),
            "neos": (CAD_URL, parse_close_approaches),
        }
        raw = await asyncio.gather(
            *(fetch_json(url) for url, _ in parsers.values()), return_exceptions=True
        )
        body: dict[str, Any] = {"kp": None, "f107": None, "neos": [], "failed": []}
        for (name, (url, parse)), got in zip(parsers.items(), raw, strict=True):
            if isinstance(got, BaseException):
                if not isinstance(got, Exception):
                    raise got  # cancellation and the like are not a source failure
                log.info("public data: %s failed (%s: %s)", url, type(got).__name__, got)
                body["failed"].append(name)
                continue
            try:
                parsed = parse(got)
            except (UpstreamError, ValueError, TypeError) as exc:
                log.info("public data: %s unusable (%s)", url, exc)
                body["failed"].append(name)
                continue
            if parsed is None:
                log.info("public data: %s held no usable reading", url)
                body["failed"].append(name)
                continue
            body[name] = parsed
        if len(body["failed"]) == len(parsers):
            raise UpstreamError("every space-weather source failed")
        return body, not body["failed"]

    return await _serve(_SPACE, refresh)


@router.get("/iss", summary="Where the International Space Station is now")
async def iss() -> dict[str, Any]:
    """``{lat, lon, alt_km, vel_kmh, visibility, time_ms}``; the upstream is
    asked at most once per ``ISS_MIN_INTERVAL_S`` seconds."""

    async def refresh() -> tuple[dict[str, Any], bool]:
        return parse_iss(await fetch_json(ISS_URL)), True

    return await _serve(_ISS, refresh)


@router.get("/stations", summary="Orbital elements of the space stations (CelesTrak)")
async def stations() -> dict[str, Any]:
    """``items``: ``{name, norad_id, epoch_ms, mean_motion, eccentricity,
    inclination, raan, arg_perigee, mean_anomaly, period_min, apogee_km,
    perigee_km}``. CelesTrak asks clients to poll no more than every two
    hours, so the answer is cached that long."""

    async def refresh() -> tuple[dict[str, Any], bool]:
        return {"items": parse_stations(await fetch_json(STATIONS_URL))}, True

    return await _serve(_STATIONS, refresh)


__all__ = [
    "fetch_json",
    "orbit_from_elements",
    "parse_close_approaches",
    "parse_events",
    "parse_f107",
    "parse_iss",
    "parse_kp",
    "parse_quakes",
    "parse_stations",
    "reset_caches",
    "router",
]
