"""The public cameras God's Eye View lists, as the camera wall reads them."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jarvis.intel import cctv

_SOURCES = {
    "sources": [
        {
            "id": "354",
            "name": "5TH ST / CONGRESS AVE",
            "city": "Austin",
            "cityId": "austin",
            "provider": "Austin Transportation & Public Works",
            "lat": 30.267,
            "lon": -97.743,
            "headingDeg": 67.5,
            "feedType": "image",
            "groundHeights": {"mountGroundM": 120},
        },
        {
            "id": "133",
            "name": "6TH ST",
            "city": "Austin",
            "cityId": "austin",
            "provider": "Austin Transportation & Public Works",
            "lat": 30.269,
            "lon": -97.741,
            "feedType": "hls",
        },
        {
            "id": "tfl-1",
            "name": "A40 Westway",
            "city": "London",
            "cityId": "london",
            "provider": "Transport for London",
            "lat": 51.52,
            "lon": -0.21,
            "feedType": "image",
        },
        {"id": "bad", "name": "No position", "lat": None, "lon": 3},
        {"id": "far", "name": "Off the map", "lat": 95, "lon": 3},
    ],
    "trimmedPacks": [],
}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    cctv.reset()
    monkeypatch.delenv("JARVIS_GEV_URL", raising=False)


def test_catalog_keeps_placeable_cameras_and_groups_them_by_city(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []

    async def fake(url: str, limit: int) -> Any:
        asked.append(url)
        return _SOURCES

    monkeypatch.setattr(cctv, "fetch_json", fake)
    body = asyncio.run(cctv.catalog(now=0.0))
    assert asked == ["http://127.0.0.1:4173/api/cctv/sources"]
    assert body["available"] is True and body["gev"] == "http://127.0.0.1:4173"
    assert [c["id"] for c in body["cameras"]] == ["354", "133", "tfl-1"]
    assert body["cameras"][1]["feed"] == "video"
    assert "groundHeights" not in body["cameras"][0]
    austin = body["cities"][0]
    assert austin["id"] == "austin" and austin["count"] == 2 and austin["video"] == 1
    assert austin["providers"] == ["Austin Transportation & Public Works"]
    asyncio.run(cctv.catalog(now=60.0))
    assert len(asked) == 1


def test_catalog_says_when_god_s_eye_view_is_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    async def down(url: str, limit: int) -> Any:
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(cctv, "fetch_json", down)
    body = asyncio.run(cctv.catalog(now=0.0))
    assert body["available"] is False and body["reason"] == "gev_down"
    assert body["cameras"] == []


def test_only_a_loopback_server_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    async def never(url: str, limit: int) -> Any:
        raise AssertionError("a remote host must not be asked")

    monkeypatch.setattr(cctv, "fetch_json", never)
    monkeypatch.setenv("JARVIS_GEV_URL", "http://example.com:4173")
    body = asyncio.run(cctv.catalog(now=0.0))
    assert body["reason"] == "not_loopback"
    monkeypatch.setenv("JARVIS_GEV_URL", "http://localhost:5000/")
    assert cctv.gev_base() == "http://localhost:5000"


def test_health_counts_each_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(url: str, limit: int) -> Any:
        return {
            "cameras": [
                {"status": "ok"},
                {"status": "ok"},
                {"status": "degraded"},
                {"status": "odd"},
            ]
        }

    monkeypatch.setattr(cctv, "fetch_json", fake)
    assert asyncio.run(cctv.health(now=0.0)) == {"ok": 2, "degraded": 1, "error": 0, "unknown": 1}
