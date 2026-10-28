"""Filtering the current incidents: one implementation for the routes and the
agent tools, so "South Asia, last 24 hours" means the same set everywhere.

Regions are plain latitude/longitude boxes. They are approximate on purpose
(a box cannot follow a border); an event inside a box is "in the region" for
filtering, never a statement about which country it belongs to.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass

from .model import Evidence, IntelEvent, Kind, category_of


@dataclass(frozen=True, slots=True)
class Region:
    id: str
    name: str
    #: One or more boxes (south, west, north, east); a box whose west is
    #: greater than its east crosses the antimeridian.
    boxes: tuple[tuple[float, float, float, float], ...]

    def contains(self, lat: float, lon: float) -> bool:
        for south, west, north, east in self.boxes:
            if not south <= lat <= north:
                continue
            if west <= east and west <= lon <= east:
                return True
            if west > east and (lon >= west or lon <= east):
                return True
        return False


REGIONS: tuple[Region, ...] = (
    Region("north-america", "North America", ((7.0, -170.0, 84.0, -50.0),)),
    Region("south-america", "South America", ((-57.0, -92.0, 13.0, -32.0),)),
    Region("europe", "Europe", ((34.0, -25.0, 72.0, 45.0),)),
    Region("africa", "Africa", ((-36.0, -26.0, 38.0, 52.0),)),
    Region("middle-east", "Middle East", ((12.0, 25.0, 42.0, 63.0),)),
    Region("south-asia", "South Asia", ((5.0, 60.0, 37.5, 97.5),)),
    Region("east-asia", "East Asia", ((18.0, 97.5, 54.0, 150.0),)),
    Region("southeast-asia", "Southeast Asia", ((-11.0, 92.0, 28.5, 141.0),)),
    Region("central-asia", "Central Asia", ((35.0, 46.0, 56.0, 88.0),)),
    Region("oceania", "Oceania", ((-50.0, 110.0, 0.0, 180.0), (-50.0, -180.0, 0.0, -130.0))),
    Region("caribbean", "Caribbean", ((9.0, -90.0, 27.5, -59.0),)),
)
REGION_BY_ID = {region.id: region for region in REGIONS}

HOURS_MAX = 24 * 30
LIMIT_MAX = 500


def region_for(name: str | None) -> Region | None:
    """A region by id or name, forgiving case, spaces and hyphens."""
    if not name:
        return None
    key = name.strip().lower().replace(" ", "-").replace("_", "-")
    if key in REGION_BY_ID:
        return REGION_BY_ID[key]
    for region in REGIONS:
        if region.name.lower() == name.strip().lower():
            return region
    return None


@dataclass(frozen=True, slots=True)
class Filter:
    hours: float | None = None
    kinds: frozenset[Kind] = frozenset()
    category: str | None = None
    region: Region | None = None
    min_severity: int | None = None
    evidence: frozenset[Evidence] = frozenset()
    #: Leave out events with no position (they cannot be shown on a globe).
    placed_only: bool = False

    def matches(self, event: IntelEvent, now_ms: int) -> bool:
        if (
            self.hours is not None
            and (event.updated_at or event.occurred_at) < now_ms - self.hours * 3_600_000
        ):
            return False
        if self.kinds and event.kind not in self.kinds:
            return False
        if self.category and category_of(event.kind) != self.category:
            return False
        if self.min_severity is not None and (
            event.severity is None or event.severity < self.min_severity
        ):
            return False
        if self.evidence and event.evidence not in self.evidence:
            return False
        if (self.region is not None or self.placed_only) and not event.has_position:
            return False
        if self.region is not None and not self.region.contains(event.lat, event.lon):  # type: ignore[arg-type]
            return False
        return True


def apply(
    events: Iterable[IntelEvent], flt: Filter, limit: int | None = None, now_ms: int | None = None
) -> list[IntelEvent]:
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    out = [event for event in events if flt.matches(event, now)]
    return out[: max(0, min(limit, LIMIT_MAX))] if limit is not None else out


def parse_kinds(values: Iterable[str] | None) -> frozenset[Kind]:
    """Known kinds only; an unknown word is ignored rather than guessed."""
    kinds = set()
    for value in values or ():
        try:
            kinds.add(Kind(value.strip().lower()))
        except ValueError:  # not a kind this service knows: no filter on it
            continue
    return frozenset(kinds)


def parse_evidence(values: Iterable[str] | None) -> frozenset[Evidence]:
    found = set()
    for value in values or ():
        try:
            found.add(Evidence(value.strip().lower()))
        except ValueError:  # not an evidence class: no filter on it
            continue
    return frozenset(found)


def regions_json() -> list[dict[str, object]]:
    return [{"id": r.id, "name": r.name, "boxes": [list(b) for b in r.boxes]} for r in REGIONS]
