"""The incident store: replace-by-source, revisions, links, cap, filters."""

from __future__ import annotations

from dataclasses import replace

from jarvis.intel import parsers, query
from jarvis.intel.model import Evidence, IntelEvent, Kind
from jarvis.intel.store import IntelStore

from . import samples


def _quake(i: int, severity: int | None = None, at: int = 1) -> IntelEvent:
    return IntelEvent(
        id=f"t:{i}",
        source="t",
        kind=Kind.EARTHQUAKE,
        title=f"q{i}",
        evidence=Evidence.MEASURED,
        occurred_at=at,
        lat=10.0,
        lon=float(i % 170),
        severity=severity,
    )


def test_replace_source_removes_what_the_source_dropped() -> None:
    store = IntelStore()
    events = parsers.parse_usgs(samples.USGS)
    assert store.replace_source("usgs", events)
    assert store.rev == 1 and len(store) == 2
    assert store.replace_source("usgs", events[:1])
    assert len(store) == 1 and store.rev == 2
    assert store.get(events[1].id) is None


def test_unchanged_refresh_keeps_the_revision() -> None:
    store = IntelStore()
    events = parsers.parse_usgs(samples.USGS)
    store.replace_source("usgs", events)
    assert not store.replace_source("usgs", parsers.parse_usgs(samples.USGS))
    assert store.rev == 1


def test_an_updated_event_bumps_the_revision_and_keeps_first_seen() -> None:
    store = IntelStore()
    events = parsers.parse_usgs(samples.USGS)
    store.replace_source("usgs", events, now_ms=100)
    changed = [replace(events[0], severity=4), events[1]]
    assert store.replace_source("usgs", changed, now_ms=200)
    assert store.record(store.get(events[0].id))["ingested_at"] == 100


def test_one_source_never_touches_another() -> None:
    store = IntelStore()
    store.replace_source("usgs", parsers.parse_usgs(samples.USGS))
    store.replace_source("gdacs", parsers.parse_gdacs(samples.GDACS))
    store.replace_source("usgs", [])
    assert len(store) == 3
    # Events claiming another source are ignored.
    store.replace_source("nhc", parsers.parse_usgs(samples.USGS))
    assert store.ids_for("nhc") == set()


def test_usgs_and_gdacs_reports_of_one_quake_are_linked_not_merged() -> None:
    store = IntelStore()
    store.replace_source("usgs", parsers.parse_usgs(samples.USGS))
    store.replace_source("gdacs", parsers.parse_gdacs(samples.GDACS))
    assert "gdacs:EQ-1572508" in store.related("usgs:us6000u3bi")
    assert "usgs:us6000u3bi" in store.related("gdacs:EQ-1572508")
    # Two USGS quakes from one source are not "the same event".
    assert "usgs:us6000u3bc" not in store.related("usgs:us6000u3bi")
    record = store.record(store.get("gdacs:EQ-1572508"))
    assert record["evidence"] == "agency" and record["related"]


def test_cap_keeps_the_most_severe_then_newest() -> None:
    store = IntelStore(cap=3)
    events = [_quake(1, 4, at=1), _quake(2, None, at=9), _quake(3, 1, at=5), _quake(4, 1, at=7)]
    store.replace_source("t", events)
    assert {e.id for e in store.events()} == {"t:1", "t:3", "t:4"}
    assert store.ids_for("t") == {"t:1", "t:3", "t:4"}


def test_filters_by_time_kind_region_severity_and_evidence() -> None:
    events = (
        parsers.parse_usgs(samples.USGS)
        + parsers.parse_gdacs(samples.GDACS)
        + parsers.cluster_gdelt(parsers.parse_gdelt_rows(samples.GDELT_ROWS))
    )
    now = samples.NOW_MS
    south_asia = query.Filter(region=query.region_for("South Asia"))
    assert [e.place for e in query.apply(events, south_asia, now_ms=now)] == [
        "New Delhi, Delhi, India"
    ]
    hazards = query.Filter(category="hazard", hours=24)
    assert {e.source for e in query.apply(events, hazards, now_ms=now)} == {"usgs", "gdacs"}
    strong = query.Filter(min_severity=4)
    assert [e.id for e in query.apply(events, strong, now_ms=now)] == ["gdacs:TC-1001338"]
    media = query.Filter(evidence=query.parse_evidence(["media", "bogus"]))
    assert {e.source for e in query.apply(events, media, now_ms=now)} == {"gdelt"}
    assert query.parse_kinds(["earthquake", "alien"]) == frozenset({Kind.EARTHQUAKE})
    assert len(query.apply(events, query.Filter(), limit=2, now_ms=now)) == 2


def test_region_crossing_the_antimeridian() -> None:
    oceania = query.region_for("oceania")
    assert oceania is not None
    assert oceania.contains(-14.1, 165.6)
    assert oceania.contains(-12.7, -163.4)
    assert not oceania.contains(40.0, 2.0)
    assert query.region_for("Atlantis") is None
