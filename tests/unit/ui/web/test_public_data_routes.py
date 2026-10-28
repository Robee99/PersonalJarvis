"""Public-data routes: what the HUD's world and space panels are served.

Every upstream is faked: ``fetch_json`` is replaced by a table of canned
answers (or the pooled client rides an ``httpx.MockTransport``), so nothing
here touches the network. The clock the cache reads is faked too.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core.http_pool import HttpClientPool
from jarvis.ui.web import public_data_routes as pdr


def _ms(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp() * 1000)


class _Upstream:
    """``fetch_json`` stand-in: canned answer (or exception) per URL, call log."""

    def __init__(self) -> None:
        self.answers: dict[str, Any] = {}
        self.calls: list[str] = []

    async def __call__(self, url: str) -> Any:
        self.calls.append(url)
        answer = self.answers.get(url, pdr.UpstreamError(f"no fake for {url}"))
        if isinstance(answer, BaseException):
            raise answer
        return answer


class _Clock:
    def __init__(self) -> None:
        self.now = 10_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    pdr.reset_caches()
    yield
    pdr.reset_caches()


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> _Upstream:
    fake = _Upstream()
    monkeypatch.setattr(pdr, "fetch_json", fake)
    return fake


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(pdr, "_monotonic", fake)
    return fake


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(pdr.router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Canned upstream answers
# ---------------------------------------------------------------------------


def _quake(qid: str, time_ms: Any, coords: Any, **props: Any) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": qid,
        "properties": {"time": time_ms, **props},
        "geometry": {"type": "Point", "coordinates": coords},
    }


USGS = {
    "type": "FeatureCollection",
    "features": [
        _quake(
            "us1",
            1_760_000_000_000,
            [-122.5, 37.7, 8.1],
            mag=3.4,
            place="10 km W of Somewhere",
            url="https://earthquake.usgs.gov/earthquakes/eventpage/us1",
            alert=None,
            tsunami=0,
            sig=178,
        ),
        _quake(
            "us2",
            1_760_000_600_000,
            [142.1, 38.3, 30.0],
            mag=6.1,
            place="Off the coast",
            url="javascript:alert(1)",
            alert="green",
            tsunami=1,
            sig=572,
        ),
        _quake("us3", 1_760_000_100_000, [-70.0, -20.0], mag=None, tsunami=0),
        _quake("bad-coords", 1_760_000_200_000, [500.0, 10.0, 1.0], mag=2.6),
        _quake("no-time", None, [10.0, 10.0, 1.0], mag=2.6),
        {"id": "no-geometry", "properties": {"time": 1, "mag": 3.0}},
        "not a feature",
    ],
}


def test_parse_quakes_newest_first_and_skips_malformed_rows() -> None:
    items = pdr.parse_quakes(USGS)

    assert [q["id"] for q in items] == ["us2", "us3", "us1"]
    newest = items[0]
    assert newest == {
        "id": "us2",
        "mag": 6.1,
        "place": "Off the coast",
        "time_ms": 1_760_000_600_000,
        "lon": 142.1,
        "lat": 38.3,
        "depth_km": 30.0,
        "url": None,  # a non-http link is dropped, never passed on
        "alert": "green",
        "tsunami": True,
        "sig": 572,
    }
    # A magnitude or depth the source did not give is None, never 0.
    assert items[1]["mag"] is None
    assert items[1]["depth_km"] is None
    assert items[2]["tsunami"] is False


EONET = {
    "events": [
        {
            "id": "EONET_1",
            "title": "Wildfire A",
            "link": "https://eonet.gsfc.nasa.gov/api/v3/events/EONET_1",
            "categories": [{"id": "wildfires", "title": "Wildfires"}],
            "geometry": [
                {"date": "2026-10-16T00:00:00Z", "type": "Point", "coordinates": [10.0, 50.0]},
                {
                    "date": "2026-10-17T00:00:00Z",
                    "type": "Polygon",
                    "coordinates": [[[0.0, 0.0], [4.0, 0.0], [4.0, 2.0], [0.0, 2.0], [0.0, 0.0]]],
                    "magnitudeValue": 1200.0,
                    "magnitudeUnit": "acres",
                },
            ],
        },
        {
            "id": "EONET_2",
            "title": "Storm B",
            "link": "https://eonet.gsfc.nasa.gov/api/v3/events/EONET_2",
            "categories": [{"id": "severeStorms", "title": "Severe Storms"}],
            "geometry": [
                {
                    "date": "2026-10-18T06:00:00Z",
                    "type": "Point",
                    "coordinates": [-80.5, 25.2],
                    "magnitudeValue": None,
                    "magnitudeUnit": None,
                }
            ],
        },
        {"id": "EONET_3", "title": "No geometry", "geometry": []},
        {
            "id": "EONET_4",
            "title": "Bad date",
            "geometry": [{"date": "yesterday", "type": "Point", "coordinates": [1, 1]}],
        },
        {
            "id": "EONET_5",
            "title": "Antimeridian fire",
            "categories": [],
            "geometry": [
                {
                    "date": "2026-10-15T00:00:00Z",
                    "type": "Polygon",
                    "coordinates": [
                        [[179.0, -17.0], [-179.0, -17.0], [-179.0, -16.0], [179.0, -16.0]]
                    ],
                }
            ],
        },
    ]
}


def test_parse_events_uses_the_newest_geometry_and_polygon_centroid() -> None:
    items = pdr.parse_events(EONET)

    assert [e["id"] for e in items] == ["EONET_2", "EONET_1", "EONET_5"]
    fire = items[1]
    # Newest geometry is the polygon; its centroid excludes the closing vertex.
    assert (fire["lon"], fire["lat"]) == (2.0, 1.0)
    assert fire["date_ms"] == _ms("2026-10-17T00:00:00")
    assert (fire["magnitude"], fire["unit"]) == (1200.0, "acres")
    assert (fire["category"], fire["category_id"]) == ("Wildfires", "wildfires")
    storm = items[0]
    assert (storm["lon"], storm["lat"]) == (-80.5, 25.2)
    assert storm["magnitude"] is None and storm["unit"] is None
    # A ring across the 180th meridian is centred on it, not on Greenwich.
    antimeridian = items[2]
    assert abs(abs(antimeridian["lon"]) - 180.0) < 1e-9
    assert antimeridian["lat"] == -16.5
    assert antimeridian["category"] is None and antimeridian["link"] is None


KP_OBJECTS = [
    {
        "time_tag": f"2026-10-{day:02d}T{hour:02d}:00:00",
        "Kp": kp,
        "a_running": 7,
        "station_count": 8,
    }
    for day, hour, kp in [
        (15 + i // 8, (i % 8) * 3, round(1 + (i % 5) * 0.33, 2)) for i in range(30)
    ]
]


def test_parse_kp_objects_keeps_the_last_24_oldest_first() -> None:
    kp = pdr.parse_kp(list(reversed(KP_OBJECTS)) + [{"time_tag": "garbage", "Kp": 3}])

    assert kp is not None
    assert len(kp["series"]) == 24
    times = [p["time_ms"] for p in kp["series"]]
    assert times == sorted(times)
    assert kp["time_ms"] == times[-1] == _ms(KP_OBJECTS[-1]["time_tag"])
    assert kp["value"] == KP_OBJECTS[-1]["Kp"]


def test_parse_kp_list_of_lists_with_header_and_space_separated_times() -> None:
    rows = [
        ["time_tag", "Kp", "a_running", "station_count"],
        ["2026-10-18 06:00:00.000", "2.33", "9", "8"],
        ["2026-10-18 09:00:00.000", "4.67", "39", "8"],
        ["2026-10-18 12:00:00.000", "n/a", "0", "0"],
    ]
    kp = pdr.parse_kp(rows)

    assert kp == {
        "value": 4.67,
        "time_ms": _ms("2026-10-18T09:00:00"),
        "series": [
            {"time_ms": _ms("2026-10-18T06:00:00"), "kp": 2.33},
            {"time_ms": _ms("2026-10-18T09:00:00"), "kp": 4.67},
        ],
    }
    assert pdr.parse_kp([["time_tag", "Kp"]]) is None


def test_parse_f107_takes_the_newest_numeric_flux() -> None:
    rows = [
        {"time_tag": "2026-10-18T20:00:00", "frequency": 2800, "flux": None},
        {"time_tag": "2026-10-18T17:00:00", "frequency": 2800, "flux": 151.2},
        {"time_tag": "2026-10-18T18:00:00", "frequency": 1415, "flux": 99.0},
        {"time_tag": "2026-10-17T20:00:00", "frequency": 2800, "flux": 148.0},
    ]
    assert pdr.parse_f107(rows) == {"value": 151.2, "time_ms": _ms("2026-10-18T17:00:00")}
    assert pdr.parse_f107([]) is None


CAD = {
    "signature": {"version": "1.5"},
    "count": 3,
    "fields": [
        "des",
        "orbit_id",
        "jd",
        "cd",
        "dist",
        "dist_min",
        "dist_max",
        "v_rel",
        "v_inf",
        "t_sigma_f",
        "h",
    ],
    "data": [
        [
            "2026 TX1",
            "3",
            "2461330.1",
            "2026-Oct-30 02:10",
            "0.0012",
            "0.001",
            "0.0013",
            "9.81234",
            "9.7",
            "< 00:01",
            "27.4",
        ],
        [
            "2026 UA",
            "1",
            "2461320.1",
            "2026-Oct-21 14:32",
            "0.0100",
            "0.009",
            "0.011",
            "14.2",
            "14.1",
            "00:05",
            None,
        ],
        ["broken", "1", "x", "not a date", "0.01", "", "", "1", "1", "", "20"],
        ["no-dist", "1", "x", "2026-Nov-01 00:00", "", "", "", "1", "1", "", "20"],
    ],
}


def test_parse_close_approaches_maps_by_field_name_and_sorts_by_time() -> None:
    neos = pdr.parse_close_approaches(CAD)

    assert [n["des"] for n in neos] == ["2026 UA", "2026 TX1"]
    first = neos[0]
    assert first["time_ms"] == _ms("2026-10-21T14:32:00")
    assert first["dist_au"] == 0.01
    assert first["dist_km"] == round(0.01 * 149_597_870.7)
    assert first["dist_ld"] == round(0.01 * 149_597_870.7 / 384_400, 2)
    assert first["v_kms"] == 14.2
    assert first["h"] is None
    assert neos[1]["v_kms"] == 9.81
    # Nothing close enough: CAD leaves "data" out entirely.
    assert pdr.parse_close_approaches({"fields": CAD["fields"], "count": 0}) == []


ISS = {
    "name": "iss",
    "id": 25544,
    "latitude": 50.11,
    "longitude": -3.5,
    "altitude": 418.2,
    "velocity": 27_580.1,
    "visibility": "daylight",
    "timestamp": 1_760_000_000,
    "units": "kilometers",
}


def test_parse_iss() -> None:
    assert pdr.parse_iss(ISS) == {
        "lat": 50.11,
        "lon": -3.5,
        "alt_km": 418.2,
        "vel_kmh": 27_580.1,
        "visibility": "daylight",
        "time_ms": 1_760_000_000_000,
    }
    with pytest.raises(pdr.UpstreamError):
        pdr.parse_iss({"latitude": None, "longitude": 1})


def _element(name: str, norad: Any, mean_motion: Any, ecc: Any) -> dict[str, Any]:
    return {
        "OBJECT_NAME": name,
        "NORAD_CAT_ID": norad,
        "EPOCH": "2026-10-18T05:42:37.123456",
        "MEAN_MOTION": mean_motion,
        "ECCENTRICITY": ecc,
        "INCLINATION": 51.6393,
        "RA_OF_ASC_NODE": 210.1,
        "ARG_OF_PERICENTER": 60.2,
        "MEAN_ANOMALY": 299.9,
        "BSTAR": 0.00023,
    }


def test_parse_stations_computes_an_iss_like_orbit() -> None:
    rows = pdr.parse_stations(
        [
            _element("ISS (ZARYA)", 25544, 15.49, 0.0004),
            _element("NO MOTION", 1, 0, 0.001),
            _element("HYPERBOLIC", 2, 15.0, 1.2),
            _element("", 3, 15.0, 0.001),
            "junk",
        ]
    )

    (iss,) = rows
    assert iss["name"] == "ISS (ZARYA)"
    assert iss["norad_id"] == 25544
    assert iss["epoch_ms"] == _ms("2026-10-18T05:42:37.123456")
    assert iss["inclination"] == 51.6393
    assert iss["period_min"] == round(1440 / 15.49, 2)
    assert 415.0 <= iss["perigee_km"] <= iss["apogee_km"] <= 425.0
    assert round(iss["apogee_km"] - iss["perigee_km"], 1) == pytest.approx(
        2 * 6798.4 * 0.0004, abs=0.2
    )


def test_a_body_of_the_wrong_shape_is_an_upstream_error() -> None:
    for parse in (pdr.parse_quakes, pdr.parse_events, pdr.parse_close_approaches):
        with pytest.raises(pdr.UpstreamError):
            parse(["not", "an", "object"])
    for parse in (pdr.parse_stations, pdr.parse_kp, pdr.parse_f107):
        with pytest.raises(pdr.UpstreamError):
            parse({"not": "a list"})


# ---------------------------------------------------------------------------
# Cache, stale fallback, unavailable
# ---------------------------------------------------------------------------


def test_quakes_route_caches_within_its_ttl(
    client: TestClient, upstream: _Upstream, clock: _Clock
) -> None:
    upstream.answers[pdr.QUAKES_URL] = USGS

    first = client.get("/api/public/quakes").json()
    clock.now += 299
    second = client.get("/api/public/quakes").json()

    assert upstream.calls == [pdr.QUAKES_URL]
    assert first == second
    assert first["available"] is True and first["stale"] is False
    assert isinstance(first["fetched_at"], float)
    assert first["source"] == "USGS Earthquake Hazards Program"
    assert first["source_url"] == "https://earthquake.usgs.gov/earthquakes/feed/"
    assert [q["id"] for q in first["items"]] == ["us2", "us3", "us1"]

    clock.now += 2  # past the 5-minute TTL
    client.get("/api/public/quakes")
    assert len(upstream.calls) == 2


def test_a_failed_refresh_serves_the_last_good_body_as_stale(
    client: TestClient, upstream: _Upstream, clock: _Clock
) -> None:
    upstream.answers[pdr.EVENTS_URL] = EONET
    good = client.get("/api/public/events").json()

    clock.now += 901
    upstream.answers[pdr.EVENTS_URL] = httpx.ConnectError("offline")
    stale = client.get("/api/public/events").json()

    assert stale["available"] is True
    assert stale["stale"] is True
    assert stale["items"] == good["items"]
    assert stale["fetched_at"] == good["fetched_at"]

    # The failed upstream is not asked again on every request ...
    client.get("/api/public/events")
    assert len(upstream.calls) == 2
    # ... but is after the retry interval, and a success clears the flag.
    clock.now += pdr.FAILURE_RETRY_S + 1
    upstream.answers[pdr.EVENTS_URL] = EONET
    fresh = client.get("/api/public/events").json()
    assert fresh["stale"] is False
    assert len(upstream.calls) == 3


@pytest.mark.parametrize(
    ("path", "empty"),
    [
        ("/api/public/quakes", {"items": []}),
        ("/api/public/events", {"items": []}),
        ("/api/public/stations", {"items": []}),
        (
            "/api/public/iss",
            {
                "lat": None,
                "lon": None,
                "alt_km": None,
                "vel_kmh": None,
                "visibility": None,
                "time_ms": None,
            },
        ),
        (
            "/api/public/space",
            {"kp": None, "f107": None, "neos": [], "failed": ["kp", "f107", "neos"]},
        ),
    ],
)
def test_nothing_cached_and_upstream_down_is_200_unavailable(
    client: TestClient, upstream: _Upstream, clock: _Clock, path: str, empty: dict[str, Any]
) -> None:
    res = client.get(path)

    assert res.status_code == 200
    body = res.json()
    for key, value in empty.items():
        assert body[key] == value
    assert body["available"] is False
    assert body["stale"] is False
    assert body["fetched_at"] is None
    assert body["source"] and body["source_url"].startswith("https://")


def test_malformed_upstream_json_counts_as_a_failure(
    client: TestClient, upstream: _Upstream, clock: _Clock
) -> None:
    upstream.answers[pdr.STATIONS_URL] = {"error": "rate limited"}
    body = client.get("/api/public/stations").json()
    assert body["available"] is False


def test_iss_upstream_is_never_called_more_than_once_per_five_seconds(
    client: TestClient, upstream: _Upstream, clock: _Clock
) -> None:
    upstream.answers[pdr.ISS_URL] = ISS

    for _ in range(10):
        body = client.get("/api/public/iss").json()
        clock.now += 0.4
    assert upstream.calls == [pdr.ISS_URL]
    assert body["lat"] == 50.11 and body["available"] is True

    clock.now += 1.1  # 5.1 s after the first call
    client.get("/api/public/iss")
    assert len(upstream.calls) == 2

    # A failure does not shorten the interval either.
    upstream.answers[pdr.ISS_URL] = httpx.ReadTimeout("slow")
    clock.now += 5.1
    assert client.get("/api/public/iss").json()["stale"] is True
    clock.now += 2
    assert client.get("/api/public/iss").json()["stale"] is True
    assert len(upstream.calls) == 3


def test_space_names_the_failed_source_and_keeps_the_rest(
    client: TestClient, upstream: _Upstream, clock: _Clock
) -> None:
    upstream.answers[pdr.KP_URL] = KP_OBJECTS
    upstream.answers[pdr.F107_URL] = httpx.ConnectError("down")
    upstream.answers[pdr.CAD_URL] = CAD

    body = client.get("/api/public/space").json()

    assert body["available"] is True
    assert body["failed"] == ["f107"]
    assert body["f107"] is None
    assert body["kp"]["value"] == KP_OBJECTS[-1]["Kp"]
    assert [n["des"] for n in body["neos"]] == ["2026 UA", "2026 TX1"]
    assert body["source"] == "NOAA SWPC · NASA/JPL CNEOS"
    assert sorted(upstream.calls) == sorted([pdr.KP_URL, pdr.F107_URL, pdr.CAD_URL])

    # An incomplete answer is retried after the failure interval, not the TTL.
    clock.now += pdr.FAILURE_RETRY_S - 1
    client.get("/api/public/space")
    assert len(upstream.calls) == 3
    clock.now += 2
    upstream.answers[pdr.F107_URL] = [{"time_tag": "2026-10-18T17:00:00", "flux": 150.0}]
    body = client.get("/api/public/space").json()
    assert body["failed"] == []
    assert body["f107"] == {"value": 150.0, "time_ms": _ms("2026-10-18T17:00:00")}


# ---------------------------------------------------------------------------
# The real fetch, over a mock transport
# ---------------------------------------------------------------------------


def _pool(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]):
    pool = HttpClientPool(
        timeout_s=1.0,
        transport=httpx.MockTransport(handler),
        client_kwargs={"headers": {"User-Agent": pdr.USER_AGENT}},
    )
    monkeypatch.setattr(pdr, "_POOL", pool)
    return pool


def test_fetch_sends_the_hud_user_agent_and_decodes_json(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=json.dumps(ISS).encode())

    _pool(monkeypatch, handler)
    body = client.get("/api/public/iss").json()

    assert body["available"] is True and body["alt_km"] == 418.2
    assert str(seen[0].url) == pdr.ISS_URL
    assert seen[0].headers["user-agent"] == "Jarvis-HUD/1.0"


def test_the_production_pool_identifies_as_the_hud() -> None:
    headers = pdr._POOL._client_kwargs["headers"]
    assert headers["User-Agent"] == "Jarvis-HUD/1.0"
    assert pdr._POOL._timeout_s <= 6.0


def test_an_oversized_or_failing_answer_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    monkeypatch.setattr(pdr, "MAX_RESPONSE_BYTES", 64)

    def handler(request: httpx.Request) -> httpx.Response:
        if "usgs" in str(request.url):
            return httpx.Response(200, content=b"[" + b"1," * 100 + b"1]")
        return httpx.Response(503, content=b"busy")

    _pool(monkeypatch, handler)
    assert client.get("/api/public/quakes").json()["available"] is False
    assert client.get("/api/public/events").json()["available"] is False


def test_concurrent_requests_share_one_upstream_call(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    import asyncio

    calls: list[str] = []

    async def slow_fetch(url: str) -> Any:
        calls.append(url)
        await asyncio.sleep(0.01)
        return USGS

    monkeypatch.setattr(pdr, "fetch_json", slow_fetch)

    async def burst() -> list[dict[str, Any]]:
        return await asyncio.gather(*(pdr.quakes() for _ in range(8)))

    bodies = asyncio.run(burst())
    assert calls == [pdr.QUAKES_URL]
    assert all(b["available"] and len(b["items"]) == 3 for b in bodies)
