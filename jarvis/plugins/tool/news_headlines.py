"""news_headlines tool: today's top stories from the outlets' own RSS feeds.

Live 2026-10-25 "What are today's top news headlines in India?" ran
search_web, which found the outlets' home pages, and Gemma 4 E4B answered
with the outlet names and "visit one of those portals". The feeds carry the
headlines themselves, free and without a key.

Risk tier: safe. It only reads public feeds.
"""

from __future__ import annotations

from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.intel.news import FEEDS_BY_REGION, fetch_feeds, interleave

DEFAULT_COUNT = 8
_MAX_COUNT = 20


def render(region: str, picked: list[dict[str, Any]], failed: list[str]) -> str:
    """Numbered headlines with their outlet, then any feed that failed."""
    lines = [f"Top {region.title()} headlines now, from the outlets' RSS feeds:"]
    lines += [f"{n}. {item['title']} ({item['source']})" for n, item in enumerate(picked, 1)]
    if failed:
        lines.append(f"Not reached: {', '.join(failed)}.")
    return "\n".join(lines)


class NewsHeadlinesTool:
    name: str = "news_headlines"
    risk_tier: str = "safe"
    description: str = (
        "Today's top news headlines, read live from free outlet RSS feeds. "
        "region: india (The Hindu, Times of India, NDTV, Indian Express) or world "
        "(BBC, Al Jazeera, NPR). Use this, not search_web, for 'news', "
        "'headlines' or 'what's happening' requests."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "region": {"type": "string", "enum": sorted(FEEDS_BY_REGION)},
            "count": {"type": "integer", "minimum": 1, "maximum": _MAX_COUNT},
        },
        "required": ["region"],
    }

    def __init__(self, http: Any | None = None) -> None:
        self._http = http

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        del ctx
        region = str((args or {}).get("region") or "world").strip().lower()
        feeds = FEEDS_BY_REGION.get(region)
        if feeds is None:
            return ToolResult(
                success=False,
                output=None,
                error=f"Unknown region {region!r}. Allowed: {', '.join(sorted(FEEDS_BY_REGION))}",
            )
        try:
            count = max(1, min(_MAX_COUNT, int((args or {}).get("count") or DEFAULT_COUNT)))
        except (TypeError, ValueError):  # a count that is not a number gets the default
            count = DEFAULT_COUNT
        if self._http is not None:
            by_source, failed = await fetch_feeds(feeds, self._http)
        else:
            import httpx  # noqa: PLC0415 - lazy (AP-26)

            async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as http:
                by_source, failed = await fetch_feeds(feeds, http)
        if not by_source:
            return ToolResult(
                success=False,
                output=None,
                error=f"No news feed answered ({', '.join(failed)}); no headlines to give.",
            )
        return ToolResult(success=True, output=render(region, interleave(by_source, count), failed))
