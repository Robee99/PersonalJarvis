"""The refresh service and the /api/intel routes, with every upstream faked."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.intel import service as svc
from jarvis.ui.web import intel_routes

from . import samples


def _zip(rows: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("20261019084500.export.CSV", "\n".join(rows))
    return buf.getvalue()


class _Upstream:
    """``fetch_bytes`` stand-in: canned bytes (or an exception) per URL."""

    def __init__(self) -> None:
        stamp = "20261019084500"
        self.answers: dict[str, object] = {
            svc.USGS_URL: json.dumps(samples.USGS).encode(),
            svc.GDACS_URL: json.dumps(samples.GDACS).encode(),
            svc.EONET_URL: json.dumps(samples.EONET).encode(),
            svc.NHC_URL: json.dumps(samples.NHC).encode(),
            svc.GDELT_LAST_URL: (
                f"63760 x http://data.gdeltproject.org/gdeltv2/{stamp}.export.CSV.zip\n"
                f"76860 y http://data.gdeltproject.org/gdeltv2/{stamp}.mentions.CSV.zip\n"
            ).encode(),
            svc.GDELT_FILE_URL.format(stamp=stamp): _zip(samples.GDELT_ROWS),
        }
        self.calls: list[str] = []

    async def __call__(self, url: str, limit: int) -> bytes:
        self.calls.append(url)
        answer = self.answers.get(url)
        if answer is None:
            request = httpx.Request("GET", url)
            raise httpx.HTTPStatusError(
                "404", request=request, response=httpx.Response(404, request=request)
            )
        if isinstance(answer, BaseException):
            raise answer
        assert isinstance(answer, bytes)
        if len(answer) > limit:
            raise svc.UpstreamError("too big")
        return answer


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    svc.reset_service()
    yield
    svc.reset_service()


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> _Upstream:
    fake = _Upstream()
    monkeypatch.setattr(svc, "fetch_bytes", fake)
    return fake


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(svc, "_monotonic", fake)
    return fake


async def test_first_refresh_loads_every_source(upstream: _Upstream, clock: _Clock) -> None:
    service = svc.IntelService()
    await service.refresh(wait_s=5)
    sources = {s["id"]: s for s in service.sources_json()}
    assert {sid: s["status"] for sid, s in sources.items()} == dict.fromkeys(sources, "ok")
    assert sources["gdelt"]["count"] == 3
    assert service.store.get("nhc:ep212026") is not None
    # GDELT asked for the newest file and then older ones, a few per refresh;
    # the missing older files (404 here) are remembered as gaps.
    gdelt_files = [u for u in upstream.calls if u.endswith(".export.CSV.zip")]
    assert len(gdelt_files) == svc.GDELT_FILES_PER_REFRESH
    assert gdelt_files[0].endswith("20261019084500.export.CSV.zip")


async def test_sources_are_not_refetched_before_they_are_due(
    upstream: _Upstream, clock: _Clock
) -> None:
    service = svc.IntelService()
    await service.refresh(wait_s=5)
    calls = len(upstream.calls)
    clock.now += 30
    await service.refresh(wait_s=5)
    assert len(upstream.calls) == calls
    clock.now += 301  # USGS (5 min) and the filling GDELT window are due
    await service.refresh(wait_s=5)
    fetched = upstream.calls[calls:]
    assert svc.USGS_URL in fetched and svc.EONET_URL not in fetched


async def test_failure_keeps_old_events_marked_stale_and_backs_off(
    upstream: _Upstream, clock: _Clock
) -> None:
    service = svc.IntelService()
    await service.refresh(wait_s=5)
    upstream.answers[svc.USGS_URL] = svc.UpstreamError("down")
    clock.now += 301
    await service.refresh(wait_s=5)
    state = service.states["usgs"]
    assert state.status == "stale" and "down" in (state.error or "")
    event = service.store.get("usgs:us6000u3bi")
    assert event is not None and service.record(event)["stale"] is True
    # Doubling backoff: the next try is not before the interval has passed.
    assert state.next_due - clock.now == pytest.approx(svc.SOURCE_BY_ID["usgs"].interval_s)
    upstream.answers[svc.USGS_URL] = svc.UpstreamError("still down")
    clock.now = state.next_due
    await service.refresh(wait_s=5)
    assert state.next_due - clock.now == pytest.approx(2 * svc.SOURCE_BY_ID["usgs"].interval_s)


async def test_a_source_that_never_answered_is_unavailable(
    upstream: _Upstream, clock: _Clock
) -> None:
    upstream.answers[svc.NHC_URL] = b"<html>maintenance</html>"
    upstream.answers[svc.EONET_URL] = b"x" * (svc.MAX_JSON_BYTES + 1)
    service = svc.IntelService()
    await service.refresh(wait_s=5)
    assert service.states["nhc"].status == "unavailable"
    assert service.states["eonet"].status == "unavailable"
    assert service.states["usgs"].status == "ok"


async def test_an_unexpected_parser_error_degrades_only_that_source(
    upstream: _Upstream, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(data: object) -> list[object]:
        raise KeyError("surprise")

    monkeypatch.setattr(svc.parsers, "parse_gdacs", broken)
    service = svc.IntelService()
    await service.refresh(wait_s=5)
    assert service.states["gdacs"].status == "unavailable"
    assert "KeyError" in (service.states["gdacs"].error or "")
    assert service.states["usgs"].status == "ok"


async def test_an_oversized_gdelt_archive_is_refused(upstream: _Upstream, clock: _Clock) -> None:
    stamp = svc.GDELT_FILE_URL.format(stamp="20261019084500")
    upstream.answers[stamp] = b"y" * (svc.MAX_ZIP_BYTES + 1)
    service = svc.IntelService()
    await service.refresh(wait_s=5)
    assert service.states["gdelt"].status == "unavailable"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(intel_routes.router)
    return TestClient(app)


def test_events_route_and_unchanged_poll(upstream: _Upstream, clock: _Clock) -> None:
    client = _client()
    body = client.get("/api/intel/events").json()
    assert body["rev"] >= 1 and not body["unchanged"]
    ids = {e["id"] for e in body["events"]}
    assert {"usgs:us6000u3bi", "gdacs:TC-1001338", "nhc:ep212026"} <= ids
    assert {s["id"] for s in body["sources"]} == {"usgs", "gdacs", "eonet", "nhc", "gdelt"}
    assert any(r["id"] == "south-asia" for r in body["regions"])
    again = client.get("/api/intel/events", params={"rev": body["rev"]}).json()
    assert again == {"rev": body["rev"], "unchanged": True}


def test_event_route_includes_related_reports_and_source(
    upstream: _Upstream, clock: _Clock
) -> None:
    client = _client()
    body = client.get("/api/intel/events/usgs:us6000u3bi").json()
    assert body["event"]["evidence"] == "measured"
    assert [r["id"] for r in body["related"]] == ["gdacs:EQ-1572508"]
    assert body["source"]["id"] == "usgs" and body["source"]["terms_url"].startswith("https://")
    assert client.get("/api/intel/events/usgs:nope").status_code == 404
    assert client.get("/api/intel/events/" + "x" * 300).status_code == 404


def test_routes_answer_with_every_upstream_down(upstream: _Upstream, clock: _Clock) -> None:
    for url in list(upstream.answers):
        upstream.answers[url] = svc.UpstreamError("down")
    body = _client().get("/api/intel/events").json()
    assert body["events"] == []
    assert {s["status"] for s in body["sources"]} == {"unavailable"}
