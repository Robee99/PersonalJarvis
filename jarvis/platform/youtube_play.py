"""Play a named video on YouTube as a reflex, in the browser the owner named.

"go to chrome open youtube and play i mean it by g eazy" is one of the most
common asks, and every model route failed it live (2026-10-26): Gemma either
said "I'm opening it" and stopped, or picked the desktop driver's browser
tools and made up a window id for them. Brahma AI (MIT, Windows-first) does
this without a model; it searches and then clicks a thumbnail found by edge
detection. Here the top result comes from YouTube's public results page (the
same lookup ``youtube_music_rest._play_public`` makes), and the watch page
opens in the owner's own Chrome, Edge or default browser, signed in as they
are. Nothing is clicked on screen, so nothing can land in the wrong window.

Only a plain ask matches: a question, a negation, or a second job after the
video ("... then turn the volume up") stays with the brain. Windows only.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_BROWSER = r"(?:my\s+|the\s+)?(?:google\s+)?(?P<{name}>chrome|edge|browser)"
_LEAD = r"^\s*(?:(?:please|jarvis|hey\s+jarvis|ok(?:ay)?|can\s+you|could\s+you)[,\s]+)*"
_JOIN = r"\s*(?:,|\band\b|\bthen\b|\band\s+then\b)?\s*"
_ASK = (
    r"(?P<verb>play|search\s+for|search|look\s+up|find)(?:\s+(?:the\s+)?(?:song|video)(?:\s+called)?)?"
)
_END = r"\s*(?:please|thanks?|now)?[.!\s]*$"
_OPEN_THEN_PLAY = re.compile(
    _LEAD
    + r"(?:(?:go\s+to|open|launch|use|in|on)\s+"
    + _BROWSER.format(name="b1")
    + _JOIN
    + r")?(?:(?:open|go\s+to|on|in)\s+)?(?:my\s+|the\s+)?youtube(?:\.com)?\s*(?:(?:in|on)\s+"
    + _BROWSER.format(name="b2")
    + r")?"
    + _JOIN
    + _ASK
    + r"\s+(?P<q>.+?)"
    + r"(?:\s+(?:in|on)\s+"
    + _BROWSER.format(name="b3")
    + r")?"
    + _END,
    re.IGNORECASE,
)
_PLAY_ON = re.compile(
    _LEAD
    + _ASK
    + r"\s+(?P<q>.+?)\s+(?:on|in|from)\s+youtube(?:\.com)?(?:\s+(?:in|on)\s+"
    + _BROWSER.format(name="b1")
    + r")?"
    + _END,
    re.IGNORECASE,
)
#: Hinglish: "youtube pe arijit singh ke gaane chalao".
_HINGLISH = re.compile(
    _LEAD
    + r"(?:(?:chrome|edge)\s+(?:me|mein|main)\s+)?youtube\s+(?:pe|par|pr|mein|me)\s+(?P<q>.+?)\s+"
    r"(?P<verb>chalao|chala\s+do|lagao|laga\s+do|bajao|baja\s+do|play\s+karo|play\s+kar\s+do)"
    + _END,
    re.IGNORECASE,
)
_NOT_PLAIN = re.compile(
    r"\?|\b(don'?t|do\s+not|never|if|unless|until|later|tomorrow)\b|\byoutube\s+music\b",
    re.IGNORECASE,
)
#: A second job after the video stays with the brain.
_SECOND_JOB = re.compile(
    r"\bthen\b|,\s*and\b|\band\s+(?:turn|set|close|open|tell|send|pause|stop|make)\b",
    re.IGNORECASE,
)

_SEARCH = "https://www.youtube.com/results"
_VIDEO_ID = re.compile(r'"videoRenderer":\{"videoId":"([A-Za-z0-9_-]{11})"')
_TITLE = re.compile(r'"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"')
_FETCH_TIMEOUT_S = 6.0


@dataclass(frozen=True)
class YouTubeAsk:
    """One plain "play / search X on YouTube" order."""

    query: str
    play: bool
    #: "chrome", "edge" or "" for the default browser.
    browser: str


def parse_youtube_ask(text: str) -> YouTubeAsk | None:
    """The video this utterance plainly asks to play or search on YouTube, else ``None``."""
    text = (text or "").strip()
    if not text or len(text) > 200 or "youtube" not in text.lower():
        return None
    hinglish = _HINGLISH.match(text)
    if hinglish is not None:
        query = hinglish.group("q").strip(" .,!")
        browser = "edge" if re.match(r"\s*edge\b", text, re.IGNORECASE) else ""
        browser = "chrome" if re.match(r"\s*chrome\b", text, re.IGNORECASE) else browser
        return YouTubeAsk(query=query, play=True, browser=browser) if query else None
    m = _OPEN_THEN_PLAY.match(text) or _PLAY_ON.match(text)
    if m is None:
        return None
    query = re.sub(r"^(?:for\s+)?", "", m.group("q").strip(" .,!"), flags=re.IGNORECASE)
    if (
        not query
        or _NOT_PLAIN.search(text)
        or _SECOND_JOB.search(query)
        or re.search(r"\byoutube\b", query, re.IGNORECASE)
    ):
        return None
    named = next(
        (m.group(g) for g in ("b1", "b2", "b3") if g in m.re.groupindex and m.group(g)), ""
    )
    browser = named.lower() if named.lower() in ("chrome", "edge") else ""
    return YouTubeAsk(query=query, play=m.group("verb").lower() == "play", browser=browser)


def top_video(query: str) -> tuple[str, str] | None:
    """``(video_id, title)`` of YouTube's top result for ``query``, or ``None``."""
    url = f"{_SEARCH}?{urllib.parse.urlencode({'search_query': query})}"
    req = urllib.request.Request(  # noqa: S310 - a fixed https host
        url,
        headers={
            "Accept-Language": "en-US,en;q=0.8",
            "Cookie": "CONSENT=YES+1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_S) as resp:  # noqa: S310 - fixed https host
            page = resp.read().decode("utf-8", "replace")
    except OSError as exc:
        log.info("youtube reflex: search for %r failed: %s", query, exc)
        return None
    found = _VIDEO_ID.search(page)
    if found is None:
        return None
    title_match = _TITLE.search(page, found.end())
    try:
        title = json.loads(f'"{title_match.group(1)}"') if title_match else query
    except ValueError:
        title = query
    return found.group(1), " ".join(str(title).split())


def _browser_exe(browser: str) -> str | None:
    """The installed Chrome or Edge, or ``None``."""
    rel = {
        "chrome": (r"Google\Chrome\Application\chrome.exe",),
        "edge": (r"Microsoft\Edge\Application\msedge.exe",),
    }.get(browser, ())
    roots = [os.environ.get(k, "") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    for root in filter(None, roots):
        for part in rel:
            path = Path(root) / part
            if path.is_file():
                return str(path)
    return None


def open_in_browser(url: str, browser: str) -> str:
    """Open ``url`` in the named browser (a new tab in its open window), else the default.

    Returns the browser that opened it, as spoken ("Chrome", "Edge", "your browser").
    """
    exe = _browser_exe(browser) if browser else None
    if exe:
        subprocess.Popen([exe, url])  # noqa: S603 - the installed browser and an https URL
        return {"chrome": "Chrome", "edge": "Edge"}[browser]
    os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606 - an https URL, opened by the browser
    return "your browser"


def youtube_reflex(
    text: str,
    *,
    lookup: Callable[[str], tuple[str, str] | None] = top_video,
    opener: Callable[[str, str], str] | None = None,
    platform: str = sys.platform,
) -> str | None:
    """Play or search a named video on YouTube, else ``None``.

    ``lookup`` and ``opener`` are injectable for tests.
    """
    ask = parse_youtube_ask(text)
    if ask is None or (opener is None and platform != "win32"):
        return None
    opener = opener or open_in_browser
    results = f"{_SEARCH}?{urllib.parse.urlencode({'search_query': ask.query})}"
    found = lookup(ask.query) if ask.play else None
    try:
        if found is None:
            where = opener(results, ask.browser)
            if ask.play:
                return (
                    f"I couldn't read YouTube's results, so I opened the search for "
                    f"{ask.query} in {where}."
                )
            return f"Opened the YouTube search for {ask.query} in {where}."
        video_id, title = found
        where = opener(f"https://www.youtube.com/watch?v={video_id}", ask.browser)
    except OSError as exc:
        log.warning("youtube reflex: opening the browser failed: %s", exc)
        return None
    return f"Opened {title} on YouTube in {where}."
