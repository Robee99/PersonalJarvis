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


# ---------------------------------------------------------------------------
# HUD to-do list
# ---------------------------------------------------------------------------


@pytest.fixture
def todo_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "hud" / "todos.json"
    monkeypatch.setattr(deck_routes, "todos_path", lambda: path)
    return path


def _clock(monkeypatch: pytest.MonkeyPatch, start_ms: int = 1_000) -> list[int]:
    """A fake wall clock: every ``_now_ms()`` call advances it by one second."""
    now = [start_ms]

    def tick() -> int:
        now[0] += 1_000
        return now[0]

    monkeypatch.setattr(deck_routes, "_now_ms", tick)
    return now


def test_todos_start_empty_without_a_file(client: TestClient, todo_file: Path):
    assert client.get("/api/deck/todos").json() == {"todos": []}
    assert not todo_file.exists()


def test_create_strips_the_title_and_persists(client: TestClient, todo_file: Path):
    res = client.post("/api/deck/todos", json={"title": "  Water the plants  "})
    assert res.status_code == 201
    todo = res.json()["todo"]
    assert todo["title"] == "Water the plants"
    assert todo["done"] is False
    assert todo["done_ms"] is None
    assert isinstance(todo["created_ms"], int)
    assert len(todo["id"]) == 32 and int(todo["id"], 16) >= 0
    import json

    stored = json.loads(todo_file.read_text(encoding="utf-8"))
    assert stored["todos"] == [todo]
    # No temp file is left beside the real one.
    assert sorted(p.name for p in todo_file.parent.iterdir()) == ["todos.json"]


def test_list_orders_open_newest_first_then_done_newest_done_first(
    client: TestClient, todo_file: Path, monkeypatch: pytest.MonkeyPatch
):
    _clock(monkeypatch)
    ids = {
        name: client.post("/api/deck/todos", json={"title": name}).json()["todo"]["id"]
        for name in ("a", "b", "c", "d")
    }
    client.patch(f"/api/deck/todos/{ids['b']}", json={"done": True})
    client.patch(f"/api/deck/todos/{ids['a']}", json={"done": True})

    titles = [t["title"] for t in client.get("/api/deck/todos").json()["todos"]]
    assert titles == ["d", "c", "a", "b"]


def test_complete_uncomplete_and_rename(
    client: TestClient, todo_file: Path, monkeypatch: pytest.MonkeyPatch
):
    now = _clock(monkeypatch)
    todo_id = client.post("/api/deck/todos", json={"title": "Draft"}).json()["todo"]["id"]

    done = client.patch(f"/api/deck/todos/{todo_id}", json={"done": True}).json()["todo"]
    assert done["done"] is True
    assert done["done_ms"] == now[0]

    renamed = client.patch(f"/api/deck/todos/{todo_id}", json={"title": " Final "}).json()["todo"]
    assert renamed["title"] == "Final"
    assert renamed["done"] is True  # an omitted field is left alone

    reopened = client.patch(f"/api/deck/todos/{todo_id}", json={"done": False}).json()["todo"]
    assert reopened["done"] is False
    assert reopened["done_ms"] is None
    assert client.get("/api/deck/todos").json()["todos"] == [reopened]


def test_delete_and_unknown_ids_are_404(client: TestClient, todo_file: Path):
    todo_id = client.post("/api/deck/todos", json={"title": "Gone soon"}).json()["todo"]["id"]
    assert client.delete(f"/api/deck/todos/{todo_id}").json() == {"ok": True}
    assert client.get("/api/deck/todos").json() == {"todos": []}

    assert client.delete(f"/api/deck/todos/{todo_id}").status_code == 404
    assert client.patch("/api/deck/todos/nope", json={"done": True}).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 201},
        {"title": 5},
        {},
        {"title": "ok", "extra": 1},
    ],
)
def test_create_rejects_bad_bodies_with_422(client: TestClient, todo_file: Path, body: dict):
    assert client.post("/api/deck/todos", json=body).status_code == 422
    assert not todo_file.exists()


def test_patch_rejects_bad_bodies_with_422(client: TestClient, todo_file: Path):
    todo_id = client.post("/api/deck/todos", json={"title": "Keep"}).json()["todo"]["id"]
    for body in ({"title": " "}, {"done": "yes"}, {"colour": "red"}):
        assert client.patch(f"/api/deck/todos/{todo_id}", json=body).status_code == 422
    assert client.post("/api/deck/todos", json={"title": "x" * 200}).status_code == 201


def test_a_full_list_refuses_with_409(
    client: TestClient, todo_file: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(deck_routes, "TODO_MAX_ITEMS", 3)
    for n in range(3):
        assert client.post("/api/deck/todos", json={"title": f"t{n}"}).status_code == 201
    res = client.post("/api/deck/todos", json={"title": "one too many"})
    assert res.status_code == 409
    assert "full" in res.json()["detail"]
    assert len(client.get("/api/deck/todos").json()["todos"]) == 3


def test_a_corrupt_file_reads_empty_and_is_moved_aside_on_write(
    client: TestClient, todo_file: Path, caplog: pytest.LogCaptureFixture
):
    todo_file.parent.mkdir(parents=True)
    todo_file.write_text("{not json", encoding="utf-8")

    with caplog.at_level("WARNING", logger=deck_routes.log.name):
        assert client.get("/api/deck/todos").json() == {"todos": []}
    assert "not valid JSON" in caplog.text

    assert client.post("/api/deck/todos", json={"title": "Fresh"}).status_code == 201
    assert [t["title"] for t in client.get("/api/deck/todos").json()["todos"]] == ["Fresh"]
    (aside,) = todo_file.parent.glob("todos.json.corrupt-*")
    assert aside.read_text(encoding="utf-8") == "{not json"


def test_malformed_rows_are_skipped(client: TestClient, todo_file: Path):
    import json

    good = {"id": "a" * 32, "title": "Good", "done": False, "created_ms": 5, "done_ms": None}
    todo_file.parent.mkdir(parents=True)
    todo_file.write_text(
        json.dumps(
            {
                "todos": [
                    good,
                    {"id": "b", "title": "No created"},
                    "not a row",
                    {"id": "c", "title": "Bad done", "done": "yes", "created_ms": 1},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert client.get("/api/deck/todos").json() == {"todos": [good]}
