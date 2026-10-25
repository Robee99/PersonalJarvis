"""Headlines from free, keyless RSS feeds, for the World screen and the brain.

The owner's rule is free sources only, so news comes from the outlets' own
public RSS feeds, never a paid news API. A feed that fails is named, never
filled in.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any

log = logging.getLogger(__name__)

#: World headlines (the World screen's broadcast).
WORLD_FEEDS: tuple[tuple[str, str], ...] = (
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("NPR World", "https://feeds.npr.org/1004/rss.xml"),
)
#: Indian national headlines; each answered with items on 2026-10-25.
INDIA_FEEDS: tuple[tuple[str, str], ...] = (
    ("The Hindu", "https://www.thehindu.com/news/national/feeder/default.rss"),
    ("Times of India", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms"),
    ("NDTV", "https://feeds.feedburner.com/ndtvnews-top-stories"),
    ("Indian Express", "https://indianexpress.com/section/india/feed/"),
)
FEEDS_BY_REGION: dict[str, tuple[tuple[str, str], ...]] = {
    "world": WORLD_FEEDS,
    "india": INDIA_FEEDS,
}
MAX_FEED_BYTES = 2_000_000


def parse_rss(source: str, raw: bytes, limit: int = 15) -> list[dict[str, Any]]:
    """The newest items of one RSS feed as ``{title, link, source, published}``.

    A feed that declares a DOCTYPE or entities is refused outright: the
    standard parser would expand them, and no news feed needs them.
    """
    head = raw[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in raw.upper():
        return []
    try:
        root = ET.fromstring(raw)  # noqa: S314 - DOCTYPE/ENTITY refused above
    except ET.ParseError:
        log.debug("news: %s feed is not well-formed XML", source, exc_info=True)
        return []
    items: list[dict[str, Any]] = []
    for node in root.iter("item"):
        title = " ".join((node.findtext("title") or "").split())
        link = (node.findtext("link") or "").strip()
        if not title or not link.startswith(("https://", "http://")):
            continue
        published: float | None = None
        stamp = node.findtext("pubDate")
        if stamp:
            try:
                published = parsedate_to_datetime(stamp).timestamp()
            except (TypeError, ValueError):  # unparseable date: unknown, never invented
                published = None
        items.append({"title": title, "link": link, "source": source, "published": published})
        if len(items) >= limit:
            break
    return items


async def fetch_feeds(
    feeds: tuple[tuple[str, str], ...], http: Any, *, limit: int = 15
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Each feed's items by source, and the sources that failed or came back empty."""
    import httpx  # noqa: PLC0415 - lazy (AP-26)

    by_source: dict[str, list[dict[str, Any]]] = {}
    failed: list[str] = []
    for source, url in feeds:
        try:
            res = await http.get(url, headers={"User-Agent": "Jarvis-HUD/1.0"})
            res.raise_for_status()
            got = parse_rss(source, res.content[:MAX_FEED_BYTES], limit)
        except httpx.HTTPError as exc:
            log.info("news: feed %s failed: %s", source, exc)
            got = []
        if got:
            by_source[source] = got
        else:
            failed.append(source)
    return by_source, failed


def interleave(by_source: dict[str, list[dict[str, Any]]], count: int) -> list[dict[str, Any]]:
    """The top story of every outlet first, then each one's second, and so on."""
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    rank = 0
    while len(picked) < count and any(rank < len(items) for items in by_source.values()):
        for items in by_source.values():
            if rank < len(items) and len(picked) < count:
                key = items[rank]["title"].lower()
                if key not in seen:
                    seen.add(key)
                    picked.append(items[rank])
        rank += 1
    return picked
