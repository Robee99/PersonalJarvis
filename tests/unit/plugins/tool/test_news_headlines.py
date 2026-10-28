from __future__ import annotations

from typing import Any

import httpx

from jarvis.intel import news
from jarvis.plugins.tool.news_headlines import NewsHeadlinesTool


def _rss(*titles: str) -> bytes:
    items = "".join(
        f"<item><title><![CDATA[{t}]]></title><link>https://example.org/{n}</link>"
        f"<pubDate>Sun, 25 Oct 2026 15:0{n}:00 GMT</pubDate></item>"
        for n, t in enumerate(titles)
    )
    return f'<?xml version="1.0"?><rss><channel>{items}</channel></rss>'.encode()


class _Http:
    """Answers each feed URL with its canned body, or a 503."""

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self.bodies = bodies

    async def get(self, url: str, **_kw: Any) -> httpx.Response:
        body = self.bodies.get(url)
        request = httpx.Request("GET", url)
        if body is None:
            return httpx.Response(503, request=request)
        return httpx.Response(200, content=body, request=request)


def _india(**bodies: bytes) -> dict[str, bytes]:
    urls = dict(news.INDIA_FEEDS)
    return {urls[name.replace("_", " ")]: body for name, body in bodies.items()}


async def test_lists_each_outlets_top_story_first() -> None:
    http = _Http(
        _india(
            The_Hindu=_rss("Snow on Kedarnath", "Hindu second"),
            NDTV=_rss("Monk praises flydubai hero"),
        )
    )

    result = await NewsHeadlinesTool(http=http).execute({"region": "india", "count": 3}, None)  # type: ignore[arg-type]

    assert result.success
    lines = result.output.splitlines()
    assert lines[1] == "1. Snow on Kedarnath (The Hindu)"
    assert lines[2] == "2. Monk praises flydubai hero (NDTV)"
    assert lines[3] == "3. Hindu second (The Hindu)"
    assert lines[-1] == "Not reached: Times of India, Indian Express."


async def test_says_so_when_no_feed_answers() -> None:
    result = await NewsHeadlinesTool(http=_Http({})).execute({"region": "world"}, None)  # type: ignore[arg-type]

    assert not result.success
    assert "no headlines" in (result.error or "")


async def test_refuses_an_unknown_region() -> None:
    result = await NewsHeadlinesTool(http=_Http({})).execute({"region": "mars"}, None)  # type: ignore[arg-type]

    assert not result.success
    assert "india" in (result.error or "")


def test_repeated_titles_are_listed_once() -> None:
    same = {"title": "Same story", "link": "https://a", "source": "A", "published": None}
    other = {"title": "SAME STORY", "link": "https://b", "source": "B", "published": None}

    assert news.interleave({"A": [same], "B": [other]}, 5) == [same]
