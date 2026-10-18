"""REST-route tests for the mission deck's pictures.

Three things must hold. The Screen-Context frame is served exactly as the
mirror holds it and disappears with it. The Computer-Use frame is addressed by
hash ONLY — a path, a filename or a short digest is refused before the
filesystem is touched, which is the whole traversal defence. And nothing is
cacheable: every image response says ``no-store``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.screen_context import last_frame as last_frame_module
from jarvis.screen_context.last_frame import LastFrameMirror
from jarvis.ui.web import deck_routes


@pytest.fixture
def mirror(monkeypatch: pytest.MonkeyPatch) -> LastFrameMirror:
    fresh = LastFrameMirror(ttl_s=120)
    monkeypatch.setattr(last_frame_module, "_mirror", fresh)
    return fresh


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deck_routes.router)
    return TestClient(app)


def test_frame_404_when_nothing_is_held(client: TestClient, mirror: LastFrameMirror):
    assert client.get("/api/deck/frame").status_code == 404
    assert client.get("/api/deck/frame/meta").json() == {"available": False, "seq": 0}


def test_frame_serves_the_held_picture_with_its_shape(client: TestClient, mirror: LastFrameMirror):
    mirror.set(
        b"\xff\xd8jpegbytes",
        mime="image/jpeg",
        width=1280,
        height=720,
        source="screen_context",
        target_label="Monitor 1",
        trace_id="abc",
    )

    meta = client.get("/api/deck/frame/meta").json()
    assert meta["available"] is True
    assert meta["seq"] == 1
    assert (meta["width"], meta["height"]) == (1280, 720)
    assert meta["target_label"] == "Monitor 1"

    res = client.get("/api/deck/frame")
    assert res.status_code == 200
    assert res.content == b"\xff\xd8jpegbytes"
    assert res.headers["content-type"].startswith("image/jpeg")
    assert res.headers["x-frame-seq"] == "1"
    assert res.headers["cache-control"] == "no-store"


def test_frame_disappears_when_the_mirror_is_cleared(client: TestClient, mirror: LastFrameMirror):
    mirror.set(b"x", mime="image/png", width=1, height=1, source="screen_context")
    assert client.get("/api/deck/frame").status_code == 200
    mirror.clear()
    assert client.get("/api/deck/frame").status_code == 404


@pytest.mark.parametrize(
    "bad",
    [
        "..%2F..%2Fetc%2Fpasswd",
        "abc",
        "A" * 64,  # upper-case hex is not the recorder's spelling
        "0" * 63,
        "0" * 65,
        "not-a-hash.jpg",
    ],
)
def test_cu_frame_refuses_anything_that_is_not_a_hash(client: TestClient, bad: str):
    res = client.get(f"/api/deck/cu-frame/{bad}")
    assert res.status_code in (400, 404)
    # The important half: a refused key never names a real file, so the
    # message is the generic one, never an OS error with a path in it. An
    # encoded slash does not even reach the handler — the router turns it
    # away first ("Not Found"), which is the same outcome one layer earlier.
    assert res.json()["detail"] in ("invalid_hash", "no_frame", "Not Found")


def test_cu_frame_serves_a_recorded_blob_by_hash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    blob_dir = tmp_path / "blobs"
    blob_dir.mkdir()
    sha = "ab" * 32
    (blob_dir / f"{sha}.jpg").write_bytes(b"\xff\xd8cuframe")
    monkeypatch.setattr(deck_routes, "CU_BLOB_DIR", blob_dir)

    res = client.get(f"/api/deck/cu-frame/{sha}")
    assert res.status_code == 200
    assert res.content == b"\xff\xd8cuframe"
    assert res.headers["content-type"].startswith("image/jpeg")
    assert res.headers["cache-control"] == "no-store"

    # A hash the recorder never wrote (or already pruned) is a plain 404.
    assert client.get(f"/api/deck/cu-frame/{'cd' * 32}").status_code == 404


def test_context_meter_reads_the_swapper(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import httpx

    def answer(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == deck_routes.SWAP_STATUS_URL
        return httpx.Response(
            200,
            json={
                "active": "qwen3-vl",
                "context": {
                    "profile": "qwen3-vl",
                    "model": "qwen3-vl-8b",
                    "used": 31000,
                    "window": 65536,
                    "at": 1.0,
                },
            },
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(answer), **kw)
    )
    monkeypatch.setattr(
        "jarvis.brain.local_servers.read_hermes_config",
        lambda: {"compression": {"enabled": True, "threshold": 0.5, "threshold_tokens": 56000}},
    )
    body = client.get("/api/deck/context").json()
    assert body == {
        "available": True,
        "used": 31000,
        "window": 65536,
        "model": "qwen3-vl-8b",
        "at": 1.0,
        "compress_at": 56000,
    }


def test_context_meter_without_a_swapper_says_so(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    import httpx

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(refuse), **kw)
    )
    assert client.get("/api/deck/context").json() == {"available": False}


RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>World</title>
<item><title>Older story</title><link>https://example.org/a</link>
<pubDate>Sat, 18 Oct 2026 08:00:00 GMT</pubDate></item>
<item><title>Newer story</title><link>https://example.org/b</link>
<pubDate>Sat, 18 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>No link</title></item>
<item><title>Script link</title><link>javascript:alert(1)</link></item>
</channel></rss>"""


def test_rss_items_are_parsed_and_unsafe_links_dropped():
    items = deck_routes.parse_rss("Test", RSS)
    assert [i["title"] for i in items] == ["Older story", "Newer story"]
    assert items[0]["source"] == "Test"
    assert items[1]["published"] > items[0]["published"]


def test_a_feed_with_entities_is_refused():
    evil = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x "boom">]><rss><channel><item><title>&x;</title><link>https://e.org</link></item></channel></rss>'
    assert deck_routes.parse_rss("Evil", evil) == []


def test_news_merges_feeds_newest_first_and_names_the_failed_ones(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    import httpx

    def answer(request: httpx.Request) -> httpx.Response:
        if "bbci" in str(request.url):
            return httpx.Response(200, content=RSS)
        return httpx.Response(503)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(answer), **kw)
    )
    monkeypatch.setattr(deck_routes, "_news_cache", {"at": 0.0, "body": None})
    body = client.get("/api/deck/news").json()
    assert [i["title"] for i in body["items"]] == ["Newer story", "Older story"]
    assert body["failed"] == ["Al Jazeera", "NPR World"]
    assert body["sources"] == ["BBC World", "Al Jazeera", "NPR World"]
