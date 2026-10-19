"""The live news channels and city cameras the World screen plays."""

from __future__ import annotations

import re

from jarvis.intel import live


def test_every_stream_names_a_channel_and_a_known_group() -> None:
    groups = {gid for gid, _ in live.CAMERA_GROUPS}
    news_groups = {gid for gid, _ in live.NEWS_GROUPS}
    keys = [s.key for s in live.STREAMS]
    assert len(keys) == len(set(keys))
    for stream in live.STREAMS:
        assert re.fullmatch(r"UC[A-Za-z0-9_-]{22}", stream.channel_id), stream.key
        assert not stream.video_id or re.fullmatch(r"[A-Za-z0-9_-]{11}", stream.video_id)
        if stream.kind == "news":
            assert stream.group in news_groups
        else:
            assert stream.group in groups and stream.place


def test_embed_uses_the_channel_unless_a_video_is_pinned() -> None:
    sky = live.find("sky")
    assert sky is not None
    assert sky.embed_path == "live_stream?channel=UCoMdktPbSTixAyNGwb-UYkQ"
    assert sky.page_url.endswith("/channel/UCoMdktPbSTixAyNGwb-UYkQ/live")
    iss = live.find("iss")
    assert iss is not None and iss.embed_path == "M3HKLzjvKPc"


def test_find_accepts_spoken_names() -> None:
    def key(text: str) -> str | None:
        hit = live.find(text)
        return hit.key if hit else None

    assert key("Al Jazeera") == "aljazeera"
    assert key("put on al jazeera english") == "aljazeera"
    assert key("Tokyo") == "tokyo-shinjuku"
    assert key("space") == "iss"
    assert key("jerusalem-western-wall") == "jerusalem-western-wall"
    assert key("") is None
    assert key("BBC") == "bbc"
    assert key("NDTV") == "ndtv"
    assert key("India Today") == "india-today"
    assert key("fox") is None


def test_catalog_lists_streams_and_groups() -> None:
    body = live.catalog_json()
    assert {g["id"] for g in body["groups"]} == {gid for gid, _ in live.CAMERA_GROUPS}
    assert {g["id"] for g in body["news_groups"]} == {"world", "india"}
    assert all(
        set(s) == {"key", "name", "short", "kind", "group", "place", "embed", "page"}
        for s in body["streams"]
    )
    assert {s["group"] for s in body["streams"] if s["kind"] == "camera"} == {
        gid for gid, _ in live.CAMERA_GROUPS
    }


_LIVE_PAGE = (
    '<html><head><link rel="canonical" href="https://www.youtube.com/watch?v=LrdY14IH8Ww">'
    "</head></html>"
)


def test_now_playing_reads_the_live_page_and_caches_it(monkeypatch) -> None:
    import asyncio

    pages: list[str] = []

    async def fake_fetch(url: str) -> str:
        pages.append(url)
        return _LIVE_PAGE

    monkeypatch.setattr(live, "fetch_page", fake_fetch)
    monkeypatch.setattr(live, "_now", {})
    assert asyncio.run(live.now_playing("ndtv", now=0.0)) == "LrdY14IH8Ww"
    assert asyncio.run(live.now_playing("ndtv", now=60.0)) == "LrdY14IH8Ww"
    assert pages == ["https://www.youtube.com/channel/UCZFMm1mMw0F81Z37aaEzTUA/live"]
    assert asyncio.run(live.now_playing("ndtv", now=live.NOW_TTL_S + 1)) == "LrdY14IH8Ww"
    assert len(pages) == 2
    assert asyncio.run(live.now_playing("no-such-channel", now=0.0)) is None


def test_now_playing_is_none_when_off_air_or_unreachable(monkeypatch) -> None:
    import asyncio

    async def off_air(url: str) -> str:
        return (
            '<link rel="canonical" href="https://www.youtube.com/channel/UC16niRr50-MSBwiO3YDb3RA">'
        )

    async def down(url: str) -> str:
        raise OSError("network unreachable")

    monkeypatch.setattr(live, "_now", {})
    monkeypatch.setattr(live, "fetch_page", off_air)
    assert asyncio.run(live.now_playing("bbc", now=0.0)) is None
    monkeypatch.setattr(live, "fetch_page", down)
    assert asyncio.run(live.now_playing("sky", now=0.0)) is None
