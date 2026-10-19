"""Upstream payloads → incidents, on real records (``samples.py``)."""

from __future__ import annotations

from jarvis.intel import parsers
from jarvis.intel.model import Evidence, Kind, clean_text, position, safe_url

from . import samples


def test_usgs_measured_quake_keeps_source_values() -> None:
    events = parsers.parse_usgs(samples.USGS)
    by_id = {e.id: e for e in events}
    quake = by_id["usgs:us6000u3bc"]
    assert quake.kind is Kind.EARTHQUAKE
    assert quake.evidence is Evidence.MEASURED
    assert (quake.lat, quake.lon) == (-14.0261, 165.7054)
    assert quake.attrs["magnitude"] == 5.9
    assert quake.attrs["depth_km"] == 10
    # M 5.9 is band 2; a green PAGER alert does not raise it.
    assert quake.severity == 2
    assert quake.links[0].url == "https://earthquake.usgs.gov/earthquakes/eventpage/us6000u3bc"
    assert not quake.automated


def test_usgs_bad_position_is_listed_unplaced_and_no_time_is_skipped() -> None:
    data = samples.fresh(samples.USGS)
    data["features"][0]["geometry"]["coordinates"] = [0, 0, 10]
    data["features"][1]["properties"]["time"] = None
    (event,) = parsers.parse_usgs(data)
    # Never placed at an invented point: it stays in the list without a marker.
    assert event.id == "usgs:us6000u3bi"
    assert (event.lat, event.lon) == (None, None) and not event.has_position


def test_usgs_drops_a_non_http_link_but_keeps_the_event() -> None:
    data = samples.fresh(samples.USGS)
    data["features"][0]["properties"]["url"] = "javascript:alert(1)"
    event = next(e for e in parsers.parse_usgs(data) if e.id == "usgs:us6000u3bi")
    assert event.links == ()


def test_quake_severity_bands_and_pager_floor() -> None:
    assert parsers.quake_severity(4.2, None) == 0
    assert parsers.quake_severity(4.6, None) == 1
    assert parsers.quake_severity(6.0, None) == 2
    assert parsers.quake_severity(5.0, "orange") == 3
    assert parsers.quake_severity(None, None) is None


def test_gdacs_points_only_one_event_per_id_and_labels_the_peak() -> None:
    events = parsers.parse_gdacs(samples.GDACS)
    ids = sorted(e.id for e in events)
    # The flood's area polygon does not become a second flood.
    assert ids == ["gdacs:EQ-1572508", "gdacs:FL-1104235", "gdacs:TC-1001338"]
    cyclone = next(e for e in events if e.id == "gdacs:TC-1001338")
    assert cyclone.kind is Kind.CYCLONE
    assert cyclone.evidence is Evidence.AGENCY
    assert cyclone.automated
    assert cyclone.severity == 4
    assert cyclone.severity_label.startswith("GDACS red alert (latest update green)")
    flood = next(e for e in events if e.id == "gdacs:FL-1104235")
    assert flood.severity_label == "GDACS green alert"  # no "Magnitude 0"
    assert flood.attrs["position"] == "area centroid"


def test_gdacs_skips_events_that_are_no_longer_current() -> None:
    data = samples.fresh(samples.GDACS)
    for feature in data["features"]:
        feature["properties"]["iscurrent"] = "false"
    assert parsers.parse_gdacs(data) == []


def test_eonet_named_storm_is_a_cyclone_with_its_latest_position() -> None:
    events = {e.id: e for e in parsers.parse_eonet(samples.EONET)}
    wati = events["eonet:EONET_25340"]
    assert wati.kind is Kind.CYCLONE
    assert (wati.lat, wati.lon) == (-12.7, -163.4)
    assert wati.severity == 2  # 40 kt
    assert wati.links[0].name == "JTWC"
    fire = events["eonet:EONET_25310"]
    assert fire.kind is Kind.WILDFIRE
    # Acres are not a severity scale: unknown, not invented.
    assert fire.severity is None
    assert fire.severity_label == "751 acres"


def test_nhc_storm_has_wind_based_severity_and_advisory_link() -> None:
    (trudy,) = parsers.parse_nhc(samples.NHC)
    assert trudy.id == "nhc:ep212026"
    assert trudy.title == "Tropical storm Trudy"
    assert trudy.severity == 2
    assert trudy.links[0].url == "https://www.nhc.noaa.gov/text/MIATCPEP1.shtml"


def test_gdelt_rows_keep_city_level_unrest_only() -> None:
    rows = parsers.parse_gdelt_rows(samples.GDELT_ROWS)
    places = sorted({r.place for r in rows})
    assert places == [
        "Hollywood, California, United States",
        "Mulhouse, Alsace, France",
        "New Delhi, Delhi, India",
    ]


def test_gdelt_clusters_are_media_reports_never_confirmed() -> None:
    events = parsers.cluster_gdelt(parsers.parse_gdelt_rows(samples.GDELT_ROWS))
    assert {e.evidence for e in events} == {Evidence.MEDIA}
    assert all(e.automated and e.severity is None for e in events)
    delhi = next(e for e in events if e.place == "New Delhi, Delhi, India")
    assert delhi.kind is Kind.PROTEST
    assert delhi.title == "Protest reported in New Delhi, Delhi, India"
    assert "not a confirmed event" in delhi.summary
    mulhouse = next(e for e in events if e.place == "Mulhouse, Alsace, France")
    # Two mentions of one article: one source, one outlet.
    assert mulhouse.attrs["articles"] == 1
    assert [link.name for link in mulhouse.links] == ["rte.ie"]


def test_gdelt_cluster_id_is_stable_as_articles_arrive() -> None:
    first = parsers.cluster_gdelt(parsers.parse_gdelt_rows(samples.GDELT_ROWS[:1]))
    more = parsers.cluster_gdelt(parsers.parse_gdelt_rows(samples.GDELT_ROWS))
    assert first[0].id in {e.id for e in more}


def test_parsers_tolerate_garbage() -> None:
    for parse in (parsers.parse_usgs, parsers.parse_gdacs, parsers.parse_eonet, parsers.parse_nhc):
        assert parse(None) == []
        assert parse({"features": "x", "events": [1, "a"], "activeStorms": {}}) == []
    assert parsers.parse_gdelt_rows(["a\tb", "", "\t" * 80]) == []


def test_guards() -> None:
    assert position(91, 0) is None
    assert position(0, 0) is None
    assert position("12.5", "-170.25") == (12.5, -170.25)
    assert position(float("nan"), 1) is None
    assert safe_url("https://user:pw@example.org/") is None
    assert safe_url("ftp://example.org/") is None
    assert safe_url("https://example.org/a b") is None
    assert clean_text("<b>Quake</b>‮ near\n\nX", 50) == "Quake near X"
    assert clean_text("x" * 300, 10) == "x" * 9 + "…"
