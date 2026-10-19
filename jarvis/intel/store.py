"""The one set of current incidents, keyed by stable id.

Each refresh of a source REPLACES that source's events: an event the source
no longer lists is removed, so nothing downstream (the panel, either globe)
keeps a marker for it. ``rev`` changes only when something a reader would see
changed, which lets clients skip an unchanged poll.

Reports of the same thing from different sources are linked, never merged:
a USGS reading and a GDACS alert for one earthquake stay two records with
their own evidence, each listing the other under ``related``.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Iterable
from typing import Any

from .model import IntelEvent, Kind

#: The most events held at once, across every source.
STORE_CAP = 3000

QUAKE_LINK_KM = 100.0
QUAKE_LINK_MS = 30 * 60 * 1000
STORM_LINK_KM = 600.0


def _now_ms() -> int:
    return int(time.time() * 1000)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def _storm_name(event: IntelEvent) -> str | None:
    # GDACS appends the season ("CHAMPI-26"); EONET and NHC do not.
    words = [_SEASON.sub("", w.strip("-,.")).lower() for w in event.title.split()]
    named = [w for w in words if w and w not in _STORM_WORDS and not w.isdigit()]
    return named[-1] if named else None


_SEASON = re.compile(r"-\d{2}$")
_STORM_WORDS = {
    "tropical",
    "storm",
    "cyclone",
    "hurricane",
    "typhoon",
    "depression",
    "subtropical",
    "potential",
    "post-tropical",
    "super",
    "severe",
    "the",
    "of",
    "in",
    "ty",
    "tc",
}


def related_pairs(events: Iterable[IntelEvent]) -> dict[str, set[str]]:
    """Which events from DIFFERENT sources describe the same thing.

    Earthquakes: within 100 km and 30 minutes. Cyclones: the same storm name
    within 600 km. Anything else is left unlinked rather than guessed.
    """
    quakes: list[IntelEvent] = []
    storms: list[IntelEvent] = []
    for event in events:
        if not event.has_position:
            continue
        if event.kind is Kind.EARTHQUAKE:
            quakes.append(event)
        elif event.kind in (Kind.CYCLONE, Kind.STORM):
            storms.append(event)
    links: dict[str, set[str]] = {}

    def tie(a: IntelEvent, b: IntelEvent) -> None:
        links.setdefault(a.id, set()).add(b.id)
        links.setdefault(b.id, set()).add(a.id)

    quakes.sort(key=lambda e: e.occurred_at)
    for i, a in enumerate(quakes):
        for b in quakes[i + 1 :]:
            if b.occurred_at - a.occurred_at > QUAKE_LINK_MS:
                break
            if a.source != b.source and distance_km(a.lat, a.lon, b.lat, b.lon) <= QUAKE_LINK_KM:  # type: ignore[arg-type]
                tie(a, b)
    named = [(e, _storm_name(e)) for e in storms]
    for i, (a, name_a) in enumerate(named):
        for b, name_b in named[i + 1 :]:
            if (
                name_a
                and name_a == name_b
                and a.source != b.source
                and distance_km(a.lat, a.lon, b.lat, b.lon) <= STORM_LINK_KM  # type: ignore[arg-type]
            ):
                tie(a, b)
    return links


class IntelStore:
    def __init__(self, cap: int = STORE_CAP) -> None:
        self.cap = cap
        self.rev = 0
        self._events: dict[str, IntelEvent] = {}
        self._by_source: dict[str, set[str]] = {}
        self._first_seen: dict[str, int] = {}
        self._related: dict[str, set[str]] = {}

    def __len__(self) -> int:
        return len(self._events)

    def get(self, event_id: str) -> IntelEvent | None:
        return self._events.get(event_id)

    def ids_for(self, source: str) -> set[str]:
        return set(self._by_source.get(source, ()))

    def replace_source(
        self, source: str, events: Iterable[IntelEvent], now_ms: int | None = None
    ) -> bool:
        """Make ``events`` the whole current set for ``source``.

        Returns whether anything changed. Duplicate ids keep the first one.
        """
        now = now_ms if now_ms is not None else _now_ms()
        fresh: dict[str, IntelEvent] = {}
        for event in events:
            if event.source == source and event.id not in fresh:
                fresh[event.id] = event
        before = self._by_source.get(source, set())
        changed = set(fresh) != before or any(
            self._events.get(event_id) != event for event_id, event in fresh.items()
        )
        if not changed:
            return False
        for gone in before - set(fresh):
            self._events.pop(gone, None)
            self._first_seen.pop(gone, None)
        for event_id, event in fresh.items():
            self._events[event_id] = event
            self._first_seen.setdefault(event_id, now)
        self._by_source[source] = set(fresh)
        self._trim()
        self._related = related_pairs(self._events.values())
        self.rev += 1
        return True

    def _trim(self) -> None:
        if len(self._events) <= self.cap:
            return
        # Keep the most severe, then the newest; drop the rest everywhere.
        ranked = sorted(
            self._events.values(),
            key=lambda e: (
                e.severity if e.severity is not None else -1,
                e.updated_at or e.occurred_at,
            ),
            reverse=True,
        )
        for event in ranked[self.cap :]:
            self._events.pop(event.id, None)
            self._first_seen.pop(event.id, None)
            self._by_source.get(event.source, set()).discard(event.id)

    def related(self, event_id: str) -> list[str]:
        return sorted(self._related.get(event_id, ()))

    def record(self, event: IntelEvent, stale: bool = False) -> dict[str, Any]:
        body = event.to_json()
        body["ingested_at"] = self._first_seen.get(event.id)
        body["related"] = self.related(event.id)
        body["stale"] = stale
        return body

    def events(self) -> list[IntelEvent]:
        return sorted(
            self._events.values(), key=lambda e: e.updated_at or e.occurred_at, reverse=True
        )
