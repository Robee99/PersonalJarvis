"""Upstream payloads → :class:`~jarvis.intel.model.IntelEvent`.

Pure functions over decoded payloads, so each source's mapping is testable
without a network. Every source here is keyless and its terms allow reuse
with attribution:

* USGS earthquakes (public domain): ``summary/4.5_week.geojson``.
* GDACS alerts (European Commission JRC / UN OCHA): ``EVENTS4APP``.
* NASA EONET natural events (NASA open data).
* NOAA National Hurricane Center active storms (public domain).
* GDELT 2.0 event files (free for any use with a citation and link).

A value a source did not give stays ``None``. A record that cannot be read is
skipped, never repaired with invented values.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from .model import (
    PLACE_MAX,
    SUMMARY_MAX,
    TITLE_MAX,
    Evidence,
    IntelEvent,
    Kind,
    SourceLink,
    clean_text,
    link,
    links,
    number,
    position,
    safe_url,
)

log = logging.getLogger(__name__)

USGS_MAX = 300
GDACS_MAX = 400
EONET_MAX = 300
NHC_MAX = 40
GDELT_MAX = 400


def _records(data: Any, key: str) -> list[dict[str, Any]]:
    items = data.get(key) if isinstance(data, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def utc_ms(value: Any) -> int | None:
    """Unix ms from epoch ms or an ISO-8601 string (no zone = UTC)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return int(value) if value > 0 else None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:  # an unreadable date is unknown, not "now"
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1000)


# ---------------------------------------------------------------------------
# USGS
# ---------------------------------------------------------------------------


def quake_severity(mag: float | None, alert: str | None) -> int | None:
    """Severity from magnitude, raised by a PAGER impact alert when given."""
    if mag is None:
        return None
    level = 0 if mag < 4.5 else 1 if mag < 5.5 else 2 if mag < 6.5 else 3 if mag < 7.5 else 4
    floor = {"yellow": 2, "orange": 3, "red": 4}.get(alert or "", 0)
    return max(level, floor)


def parse_usgs(data: Any) -> list[IntelEvent]:
    out: list[IntelEvent] = []
    for feature in _records(data, "features"):
        props = _dict(feature.get("properties"))
        geometry = _dict(feature.get("geometry"))
        coords = geometry.get("coordinates")
        native = clean_text(feature.get("id"), 40)
        occurred = utc_ms(props.get("time"))
        if not native or occurred is None or not isinstance(coords, list) or len(coords) < 2:
            continue
        point = position(coords[1], coords[0])
        mag = number(props.get("mag"))
        alert = (
            props.get("alert")
            if props.get("alert") in ("green", "yellow", "orange", "red")
            else None
        )
        place = clean_text(props.get("place"), PLACE_MAX)
        depth = number(coords[2]) if len(coords) > 2 else None
        title = f"M {mag:.1f} earthquake" if mag is not None else "Earthquake"
        out.append(
            IntelEvent(
                id=f"usgs:{native}",
                source="usgs",
                kind=Kind.EARTHQUAKE,
                title=clean_text(f"{title} — {place}" if place else title, TITLE_MAX) or title,
                evidence=Evidence.MEASURED,
                occurred_at=occurred,
                updated_at=utc_ms(props.get("updated")),
                lat=point[0] if point else None,
                lon=point[1] if point else None,
                place=place,
                severity=quake_severity(mag, alert),
                severity_label=(f"M {mag:.1f}" if mag is not None else None),
                # "automatic" = located by software, not yet reviewed by a
                # seismologist.
                automated=props.get("status") == "automatic",
                links=links(link("USGS event page", props.get("url"))),
                attrs={
                    "magnitude": mag,
                    "depth_km": depth,
                    "pager_alert": alert,
                    "tsunami_flag": props.get("tsunami") == 1,
                    "review_status": clean_text(props.get("status"), 20),
                },
            )
        )
    out.sort(key=lambda e: e.occurred_at, reverse=True)
    return out[:USGS_MAX]


# ---------------------------------------------------------------------------
# GDACS
# ---------------------------------------------------------------------------

_GDACS_KIND = {
    "EQ": Kind.EARTHQUAKE,
    "TC": Kind.CYCLONE,
    "FL": Kind.FLOOD,
    "VO": Kind.VOLCANO,
    "WF": Kind.WILDFIRE,
    "DR": Kind.DROUGHT,
}
_GDACS_LEVEL = {"green": 1, "orange": 3, "red": 4}
#: Area events whose point is the area's centroid, not where something is.
_GDACS_AREA = {"FL", "DR"}


def parse_gdacs(data: Any) -> list[IntelEvent]:
    best: dict[str, tuple[int, IntelEvent]] = {}
    for feature in _records(data, "features"):
        props = _dict(feature.get("properties"))
        geometry = _dict(feature.get("geometry"))
        # The list carries each event's footprint polygons too; the point is
        # the event itself.
        if geometry.get("type") != "Point":
            continue
        etype = props.get("eventtype")
        kind = _GDACS_KIND.get(etype) if isinstance(etype, str) else None
        eventid = props.get("eventid")
        if kind is None or isinstance(eventid, bool) or not isinstance(eventid, int | str):
            continue
        if str(props.get("iscurrent", "true")).lower() == "false":
            continue
        occurred = utc_ms(props.get("fromdate"))
        if occurred is None:
            continue
        coords = geometry.get("coordinates")
        point = (
            position(coords[1], coords[0])
            if isinstance(coords, list) and len(coords) >= 2
            else None
        )
        level_text = str(props.get("alertlevel") or "").lower()
        level = _GDACS_LEVEL.get(level_text)
        sev = _dict(props.get("severitydata"))
        severity_text = clean_text(sev.get("severitytext"), 120)
        if not number(sev.get("severity")):
            severity_text = None  # GDACS writes "Magnitude 0" when it has no measure
        episode_text = str(props.get("episodealertlevel") or "").lower()
        latest = _GDACS_LEVEL.get(episode_text)
        name = clean_text(props.get("name") or props.get("description"), TITLE_MAX)
        native = f"{etype}-{eventid}"
        raw_episode = props.get("episodeid")
        episode = raw_episode if isinstance(raw_episode, int) else 0
        event = IntelEvent(
            id=f"gdacs:{native}",
            source="gdacs",
            kind=kind,
            title=name or f"GDACS {etype} event",
            summary=clean_text(props.get("htmldescription"), SUMMARY_MAX),
            evidence=Evidence.AGENCY,
            # GDACS alert levels are computed by its impact models.
            automated=True,
            occurred_at=occurred,
            updated_at=utc_ms(props.get("datemodified")),
            lat=point[0] if point else None,
            lon=point[1] if point else None,
            country=clean_text(props.get("country"), PLACE_MAX),
            severity=level,
            # The event's level is its peak so far; when the latest update is
            # lower, the label says so instead of implying it is still at peak.
            severity_label=(
                f"GDACS {level_text} alert"
                + (
                    f" (latest update {episode_text})"
                    if latest is not None and latest < level
                    else ""
                )
                + (f" · {severity_text}" if severity_text else "")
                if level is not None
                else severity_text
            ),
            links=links(
                link(
                    "GDACS report",
                    (props.get("url") or {}).get("report")
                    if isinstance(props.get("url"), dict)
                    else None,
                )
            ),
            attrs={
                "alert_level": level_text or None,
                "latest_alert_level": episode_text or None,
                "alert_score": number(props.get("alertscore")),
                "severity_value": number(sev.get("severity")),
                "severity_unit": clean_text(sev.get("severityunit"), 20),
                "reporting_source": clean_text(props.get("source"), 60),
                "position": "area centroid" if etype in _GDACS_AREA else "event point",
            },
        )
        held = best.get(native)
        if held is None or episode >= held[0]:
            best[native] = (episode, event)
    out = [event for _, event in best.values()]
    out.sort(key=lambda e: ((e.severity or 0), e.occurred_at), reverse=True)
    return out[:GDACS_MAX]


# ---------------------------------------------------------------------------
# NASA EONET
# ---------------------------------------------------------------------------

_EONET_KIND = {
    "wildfires": Kind.WILDFIRE,
    "severeStorms": Kind.STORM,
    "volcanoes": Kind.VOLCANO,
    "floods": Kind.FLOOD,
    "earthquakes": Kind.EARTHQUAKE,
    "landslides": Kind.LANDSLIDE,
    "seaLakeIce": Kind.ICE,
    "dustHaze": Kind.DUST,
    "drought": Kind.DROUGHT,
}
_CYCLONE_WORDS = ("typhoon", "hurricane", "cyclone", "tropical")


def wind_severity(knots: float | None) -> int | None:
    """Tropical cyclone severity from sustained wind (kt)."""
    if knots is None:
        return None
    return 1 if knots < 34 else 2 if knots < 64 else 3 if knots < 96 else 4


def _centroid(geometry: dict[str, Any]) -> tuple[float, float] | None:
    coords = geometry.get("coordinates")
    if geometry.get("type") == "Point" and isinstance(coords, list) and len(coords) >= 2:
        return position(coords[1], coords[0])
    if geometry.get("type") == "Polygon" and isinstance(coords, list) and coords:
        ring = (
            [p for p in coords[0] if isinstance(p, list) and len(p) >= 2]
            if isinstance(coords[0], list)
            else []
        )
        lons = [number(p[0]) for p in ring]
        lats = [number(p[1]) for p in ring]
        if ring and None not in lons and None not in lats:
            return position(sum(lats) / len(lats), sum(lons) / len(lons))  # type: ignore[arg-type]
    return None


def parse_eonet(data: Any) -> list[IntelEvent]:
    out: list[IntelEvent] = []
    for item in _records(data, "events"):
        native = clean_text(item.get("id"), 40)
        title = clean_text(item.get("title"), TITLE_MAX)
        cats = [c for c in item.get("categories") or [] if isinstance(c, dict)]
        cat = cats[0].get("id") if cats else None
        geometries = [g for g in item.get("geometry") or [] if isinstance(g, dict)]
        dated: list[tuple[int, dict[str, Any]]] = []
        for geometry in geometries:
            ms = utc_ms(geometry.get("date"))
            if ms is not None:
                dated.append((ms, geometry))
        if not native or not title or not dated:
            continue
        dated.sort(key=lambda pair: pair[0])
        first_ms, latest_ms, latest = dated[0][0], dated[-1][0], dated[-1][1]
        kind = _EONET_KIND.get(cat, Kind.OTHER) if isinstance(cat, str) else Kind.OTHER
        if kind is Kind.STORM and any(word in title.lower() for word in _CYCLONE_WORDS):
            kind = Kind.CYCLONE
        point = _centroid(latest)
        value = number(latest.get("magnitudeValue"))
        unit = clean_text(latest.get("magnitudeUnit"), 20)
        severity = wind_severity(value) if unit == "kts" else None
        sources = [s for s in item.get("sources") or [] if isinstance(s, dict)]
        out.append(
            IntelEvent(
                id=f"eonet:{native}",
                source="eonet",
                kind=kind,
                title=title,
                summary=clean_text(item.get("description"), SUMMARY_MAX),
                evidence=Evidence.AGENCY,
                occurred_at=first_ms,
                updated_at=latest_ms,
                lat=point[0] if point else None,
                lon=point[1] if point else None,
                severity=severity,
                severity_label=(f"{value:g} {unit}" if value is not None and unit else None),
                links=links(
                    *(link(clean_text(s.get("id"), 30) or "Source", s.get("url")) for s in sources)
                ),
                attrs={
                    "eonet_category": clean_text(cat, 40),
                    "magnitude_value": value,
                    "magnitude_unit": unit,
                    "track_points": len(dated),
                },
            )
        )
    out.sort(key=lambda e: e.updated_at or e.occurred_at, reverse=True)
    return out[:EONET_MAX]


# ---------------------------------------------------------------------------
# NOAA NHC
# ---------------------------------------------------------------------------

_NHC_CLASS = {
    "TD": "Tropical depression",
    "STD": "Subtropical depression",
    "TS": "Tropical storm",
    "STS": "Subtropical storm",
    "HU": "Hurricane",
    "PTC": "Potential tropical cyclone",
    "PC": "Post-tropical cyclone",
    "TY": "Typhoon",
}


def parse_nhc(data: Any) -> list[IntelEvent]:
    out: list[IntelEvent] = []
    for storm in _records(data, "activeStorms"):
        native = clean_text(storm.get("id"), 20)
        name = clean_text(storm.get("name"), 60)
        updated = utc_ms(storm.get("lastUpdate"))
        if not native or not name or updated is None:
            continue
        raw_cls = storm.get("classification")
        cls = raw_cls if isinstance(raw_cls, str) else ""
        label = _NHC_CLASS.get(cls, "Tropical cyclone")
        wind = number(storm.get("intensity"))
        point = position(storm.get("latitudeNumeric"), storm.get("longitudeNumeric"))
        advisory = _dict(storm.get("publicAdvisory"))
        out.append(
            IntelEvent(
                id=f"nhc:{native}",
                source="nhc",
                kind=Kind.CYCLONE,
                title=f"{label} {name}",
                evidence=Evidence.AGENCY,
                occurred_at=updated,
                updated_at=updated,
                lat=point[0] if point else None,
                lon=point[1] if point else None,
                severity=wind_severity(wind),
                severity_label=(f"{wind:g} kt sustained wind" if wind is not None else None),
                links=links(
                    link("NHC public advisory", advisory.get("url")),
                    SourceLink("National Hurricane Center", "https://www.nhc.noaa.gov/"),
                ),
                attrs={
                    "classification": cls or None,
                    "wind_kt": wind,
                    "pressure_mb": number(storm.get("pressure")),
                    "movement_dir_deg": number(storm.get("movementDir")),
                    "movement_speed_mph": number(storm.get("movementSpeed")),
                },
            )
        )
    return out[:NHC_MAX]


# ---------------------------------------------------------------------------
# GDELT 2.0 events
# ---------------------------------------------------------------------------

#: Column positions in a GDELT 2.0 event export row (61 tab-separated fields).
G_ID, G_DAY, G_CODE, G_ROOT = 0, 1, 26, 28
G_MENTIONS, G_SOURCES, G_ARTICLES = 31, 32, 33
G_GEO_TYPE, G_GEO_NAME, G_GEO_COUNTRY = 51, 52, 53
G_LAT, G_LON, G_FEATURE, G_ADDED, G_URL = 56, 57, 58, 59, 60
G_COLUMNS = 61

#: CAMEO root codes kept: 14 protest; 18 assault, 19 fight, 20 mass violence.
_GDELT_KIND = {"14": Kind.PROTEST, "18": Kind.VIOLENCE, "19": Kind.VIOLENCE, "20": Kind.VIOLENCE}
#: Geo precision kept: 3 US city, 4 world city. Country (1), US state (2) and
#: world region (5) points are centroids of an area, so they are not placed.
_GDELT_PLACED = {"3", "4"}


@dataclass(slots=True)
class GdeltRow:
    """One machine-coded event mention from a GDELT export file."""

    kind: Kind
    code: str
    day: str
    place: str | None
    country: str | None
    lat: float
    lon: float
    feature: str
    added_ms: int
    url: str
    mentions: int


def _gdelt_added(value: str) -> int | None:
    try:
        moment = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:  # not a GDELT timestamp: skip the row
        return None
    return int(moment.timestamp() * 1000)


def parse_gdelt_rows(lines: Iterable[str]) -> list[GdeltRow]:
    """The protest and violence rows with a city-level place."""
    rows: list[GdeltRow] = []
    for fields in csv.reader(lines, delimiter="\t", quoting=csv.QUOTE_NONE):
        if len(fields) < G_COLUMNS:
            continue
        kind = _GDELT_KIND.get(fields[G_ROOT])
        if kind is None or fields[G_GEO_TYPE] not in _GDELT_PLACED:
            continue
        point = position(fields[G_LAT], fields[G_LON])
        url = safe_url(fields[G_URL])
        added = _gdelt_added(fields[G_ADDED])
        if point is None or url is None or added is None or not fields[G_FEATURE]:
            continue
        mentions = number(fields[G_MENTIONS])
        rows.append(
            GdeltRow(
                kind=kind,
                code=fields[G_CODE][:4],
                day=fields[G_DAY][:8],
                place=clean_text(fields[G_GEO_NAME], PLACE_MAX),
                country=clean_text(fields[G_GEO_COUNTRY], 4),
                lat=point[0],
                lon=point[1],
                feature=fields[G_FEATURE][:40],
                added_ms=added,
                url=url,
                mentions=int(mentions) if mentions is not None else 0,
            )
        )
    return rows


@dataclass(slots=True)
class _Cluster:
    rows: list[GdeltRow] = field(default_factory=list)


def _domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def cluster_gdelt(rows: Iterable[GdeltRow]) -> list[IntelEvent]:
    """One event per kind, place and day, with every distinct article.

    The id (kind, GDELT place feature, day) stays the same while new files add
    articles to it, so a refresh updates the marker instead of adding one.
    """
    clusters: dict[str, _Cluster] = {}
    for row in rows:
        key = f"{row.kind.value}-{row.feature}-{row.day}"
        clusters.setdefault(key, _Cluster()).rows.append(row)
    out: list[IntelEvent] = []
    for key, cluster in clusters.items():
        rows_ = sorted(cluster.rows, key=lambda r: r.added_ms)
        first = rows_[0]
        urls: dict[str, str] = {}
        for row in rows_:
            urls.setdefault(row.url, _domain(row.url))
        outlets = sorted(set(urls.values()))
        word = "Protest" if first.kind is Kind.PROTEST else "Violence"
        where = first.place or "an unnamed place"
        out.append(
            IntelEvent(
                id=f"gdelt:{key}",
                source="gdelt",
                kind=first.kind,
                title=clean_text(f"{word} reported in {where}", TITLE_MAX) or word,
                summary=(
                    f"Machine-coded from {len(urls)} news article"
                    f"{'s' if len(urls) != 1 else ''} ({len(outlets)} outlet"
                    f"{'s' if len(outlets) != 1 else ''}) by the GDELT Project. "
                    "A media report, not a confirmed event."
                ),
                evidence=Evidence.MEDIA,
                automated=True,
                occurred_at=first.added_ms,
                updated_at=rows_[-1].added_ms,
                lat=first.lat,
                lon=first.lon,
                place=first.place,
                country=first.country,
                links=links(*(SourceLink(domain, url) for url, domain in urls.items())),
                attrs={
                    "articles": len(urls),
                    "outlets": len(outlets),
                    "mentions": sum(r.mentions for r in rows_),
                    "cameo_codes": sorted({r.code for r in rows_})[:6],
                },
            )
        )
    out.sort(key=lambda e: (e.attrs["outlets"], e.attrs["mentions"]), reverse=True)
    return out[:GDELT_MAX]
