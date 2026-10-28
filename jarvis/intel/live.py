"""Live video for the World screen: news channels and public city cameras.

Every stream is a public YouTube live stream played in YouTube's own embedded
player, and needs no key. A stream is named by its channel. When a channel
runs several live streams at once, YouTube's ``live_stream?channel=`` embed
does not resolve, so ``now_playing`` reads which video the channel's public
live page points at (cached, no key, nothing about the person sent). An entry
may also pin one video id, checked live and embeddable on 2026-10-19. A stream
that ends shows YouTube's "unavailable" card; the panel says so and links the
channel's live page.

News comes in groups: world, India, finance and AI. A channel whose live page
points at one-off event streams names its main stream by a word in its title
(``title_hint``). The AI channels stream only at times, so they are marked
``occasional`` and the panel says when one is off air.

Cameras are fixed views of skylines, landmarks, ports, bridges and airports
run by their own channels. Re-broadcasts of other people's cameras are left
out, so what the panel calls a camera is that camera.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

from jarvis.core.http_pool import HttpClientPool

log = logging.getLogger(__name__)

StreamKind = Literal["news", "camera"]


@dataclass(frozen=True, slots=True)
class LiveStream:
    key: str
    name: str
    kind: StreamKind
    #: News group ("world", "india", "finance", "ai") or camera group ("middle-east",
    #: "europe", "americas", "asia", "space").
    group: str
    channel_id: str
    #: Set when the channel's live page does not resolve to one stream.
    video_id: str = ""
    #: Where a camera looks, in a few words.
    place: str = ""
    #: A shorter name for a channel button, when the name is long.
    short: str = ""
    #: Words in the title of the channel's round-the-clock stream, for a
    #: channel whose live page points at a different stream (a one-off event)
    #: while the main one runs beside it.
    title_hint: str = ""
    #: Live only at times (daily shows, keynotes, conferences). When it is
    #: off air the panel says so instead of loading the player.
    occasional: bool = False

    @property
    def embed_path(self) -> str:
        """The path after ``/embed/`` on YouTube's player."""
        if self.video_id:
            return self.video_id
        return f"live_stream?channel={self.channel_id}"

    @property
    def page_url(self) -> str:
        if self.video_id:
            return f"https://www.youtube.com/watch?v={self.video_id}"
        return f"https://www.youtube.com/channel/{self.channel_id}/live"

    def json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "short": self.short or self.name,
            "kind": self.kind,
            "group": self.group,
            "place": self.place,
            "embed": self.embed_path,
            "page": self.page_url,
            "occasional": self.occasional,
        }


NEWS_GROUPS: tuple[tuple[str, str], ...] = (
    ("world", "World"),
    ("india", "India"),
    ("finance", "Finance"),
    ("ai", "AI & Tech"),
)

CAMERA_GROUPS: tuple[tuple[str, str], ...] = (
    ("middle-east", "Middle East"),
    ("europe", "Europe"),
    ("americas", "Americas"),
    ("asia", "Asia"),
    ("space", "Space"),
)

STREAMS: tuple[LiveStream, ...] = (
    # News channels that stream around the clock on YouTube.
    LiveStream(
        "aljazeera",
        "Al Jazeera English",
        "news",
        "world",
        "UCNye-wNBqNL5ZzHSJj3l8Bg",
        short="Al Jazeera",
    ),
    LiveStream("bbc", "BBC News", "news", "world", "UC16niRr50-MSBwiO3YDb3RA"),
    LiveStream("sky", "Sky News", "news", "world", "UCoMdktPbSTixAyNGwb-UYkQ"),
    LiveStream("dw", "DW News", "news", "world", "UCknLrEdhRCp1aegoMqRaCZg"),
    LiveStream(
        "france24",
        "France 24 English",
        "news",
        "world",
        "UCQfwfsi5VrQ8yKZ-UWmAEFg",
        short="France 24",
    ),
    LiveStream(
        "euronews",
        "Euronews English",
        "news",
        "world",
        "UCSrZ3UV4jOidv8ppoVuvW9Q",
        "pykpO5kQJ98",
        short="Euronews",
    ),
    LiveStream("trt", "TRT World", "news", "world", "UC7fWeaHhqgM4Ry-RMpM2YYw"),
    LiveStream("cna", "CNA", "news", "world", "UC83jt4dlz1Gjl58fzQrrKZg"),
    LiveStream(
        "nhk", "NHK World-Japan", "news", "world", "UCSPEjw8F2nQDtmUKPFNF7_A", short="NHK World"
    ),
    LiveStream("cgtn", "CGTN", "news", "world", "UCgrNz-aDmcr2uuto8_DL2jg"),
    LiveStream(
        "i24", "i24NEWS English", "news", "world", "UCvHDpsWKADrDia0c99X37vg", short="i24NEWS"
    ),
    LiveStream("africanews", "Africanews", "news", "world", "UC1_E8NeF5QHY2dtdLRBCCLA"),
    LiveStream(
        "abc-australia",
        "ABC News Australia",
        "news",
        "world",
        "UCVgO39Bk5sMo66-6o6Spn6Q",
        short="ABC Australia",
    ),
    LiveStream("reuters", "Reuters", "news", "world", "UChqUTb7kYRX8-EiaN3XFrSQ"),
    LiveStream(
        "cbs", "CBS News 24/7", "news", "world", "UC8p1vwvWtl6T73JiExfWs1g", short="CBS News"
    ),
    LiveStream(
        "nbc", "NBC News NOW", "news", "world", "UCeY0bbntWzzVIaj2z3QigXg", short="NBC News"
    ),
    LiveStream("gbnews", "GB News", "news", "world", "UC0vn8ISa4LKMunLbzaXLnOQ"),
    # Indian channels. Most run several streams at once, so they play
    # through ``now_playing``.
    LiveStream("ndtv", "NDTV 24x7", "news", "india", "UCZFMm1mMw0F81Z37aaEzTUA"),
    LiveStream("india-today", "India Today", "news", "india", "UCYPvAwZP8pZhSMW8qs7cVCw"),
    LiveStream("wion", "WION", "news", "india", "UC_gUM8rL-Lrg6O3adPW9K1g"),
    LiveStream("times-now", "Times Now", "news", "india", "UC6RJ7-PaXg6TIH2BzZfTV7w"),
    LiveStream(
        "republic", "Republic World", "news", "india", "UCwqusr8YDwM-3mEYTDeJHzw", short="Republic"
    ),
    LiveStream("cnn-news18", "CNN-News18", "news", "india", "UCef1-8eOpJgud7szVPlZQAQ"),
    LiveStream("mirror-now", "Mirror Now", "news", "india", "UCWCEYVwSqr7Epo6sSCfUgiw"),
    LiveStream("firstpost", "Firstpost", "news", "india", "UCz8QaiQxApLq8sLNcszYyJw"),
    LiveStream("dd-news", "DD News", "news", "india", "UCKwucPzHZ7zCUIf7If-Wo1g"),
    LiveStream("sansad", "Sansad TV", "news", "india", "UCISgnSNwqQ2i8lhCun3KtQg"),
    LiveStream("ani", "ANI News", "news", "india", "UCtFQDgA8J8_iiwc5-KoAQlg", short="ANI"),
    LiveStream("aaj-tak", "Aaj Tak", "news", "india", "UCt4t-jeY85JegMlZ-E5UWtA"),
    LiveStream("abp-news", "ABP News", "news", "india", "UCRWFSbif-RFENbBrSiez1DA"),
    LiveStream("zee-news", "Zee News", "news", "india", "UCIvaYmXn910QMdemBG3v1pQ"),
    LiveStream("ndtv-india", "NDTV India", "news", "india", "UC9CYT9gSNLevX5ey2_6CK0Q"),
    LiveStream("news18-india", "News18 India", "news", "india", "UCPP3etACgdUWvizcES1dJ8Q"),
    LiveStream(
        "bbc-hindi",
        "BBC News Hindi",
        "news",
        "india",
        "UCN7B-QD0Qgn2boVH5Q0pOWg",
        short="BBC Hindi",
    ),
    # Markets and business. The Indian business channels run their
    # round-the-clock stream next to one-off event streams, so they name it by
    # its title.
    LiveStream(
        "bloomberg",
        "Bloomberg Television",
        "news",
        "finance",
        "UCIALMKvObZNtJ6AmdCLP7Lg",
        short="Bloomberg",
    ),
    LiveStream("yahoo-finance", "Yahoo Finance", "news", "finance", "UCEAZeUIeJs0IjQiqTCdVSIg"),
    LiveStream(
        "schwab", "Schwab Network", "news", "finance", "UCqoSrYgusd8ZddtMoWhjHYA", short="Schwab"
    ),
    LiveStream(
        "cnbc-tv18", "CNBC-TV18", "news", "finance", "UCmRbHAgG2k2vDUvb3xsEunQ", title_hint="24x7"
    ),
    LiveStream(
        "cnbc-awaaz",
        "CNBC Awaaz",
        "news",
        "finance",
        "UCQIycDaLsBpMKjOCeaKUYVg",
        title_hint="24x7",
    ),
    LiveStream(
        "et-now", "ET Now", "news", "finance", "UCI_mwTKUhicNzFrhm33MzBQ", title_hint="live tv"
    ),
    LiveStream(
        "zee-business",
        "Zee Business",
        "news",
        "finance",
        "UCkXopQ3ubd-rnXnStZqCl2w",
        title_hint="24x7",
    ),
    LiveStream(
        "ndtv-profit",
        "NDTV Profit",
        "news",
        "finance",
        "UC3uJIdRFTGgLWrUziaHbzrg",
        title_hint="live tv",
    ),
    # AI and technology. No channel streams this around the clock, so these
    # are the ones that go live often: a weekday show, then the labs and
    # conferences when they present.
    LiveStream("tbpn", "TBPN", "news", "ai", "UC-DRzaGnL_vtBUpCFH5M0tg", occasional=True),
    LiveStream(
        "daily-ai-show",
        "The Daily AI Show",
        "news",
        "ai",
        "UCe-19rSBRgIciETwZz93OFQ",
        short="Daily AI Show",
        occasional=True,
    ),
    LiveStream(
        "hugging-face", "Hugging Face", "news", "ai", "UCHlNU7kIZhRgSbhHvFoy72w", occasional=True
    ),
    LiveStream(
        "ai-engineer", "AI Engineer", "news", "ai", "UCLKPca3kwwd-B59HNr-_lvA", occasional=True
    ),
    LiveStream("openai", "OpenAI", "news", "ai", "UCXZCJLdBC09xxGZ6gcdrc6A", occasional=True),
    LiveStream("nvidia", "NVIDIA", "news", "ai", "UCHuiy8bXnmK5nisYHUd1J5g", occasional=True),
    LiveStream(
        "google-developers",
        "Google for Developers",
        "news",
        "ai",
        "UC_x5XG1OV2P6uZZ5FSM9Ttw",
        short="Google Dev",
        occasional=True,
    ),
    # Cameras.
    LiveStream(
        "jerusalem-western-wall",
        "Jerusalem",
        "camera",
        "middle-east",
        "UCvcdHbNAQvbe2GuIDLhlwaw",
        "LMHUcDktP-w",
        "Western Wall plaza",
    ),
    LiveStream(
        "istanbul-galata",
        "Istanbul",
        "camera",
        "middle-east",
        "UCrRYP4a33Fvvd9j6TLtzj_Q",
        place="Golden Horn and Galata",
    ),
    LiveStream(
        "london-notting-hill",
        "London",
        "camera",
        "europe",
        "UC4-aIBtpNAPqEcMhAyFN6iQ",
        place="Notting Hill Gate",
    ),
    LiveStream(
        "london-heathrow",
        "London Heathrow",
        "camera",
        "europe",
        "UC6q_hfBThkGdmQ5Vb4kWjWA",
        place="Airport runways",
    ),
    LiveStream(
        "amsterdam-ijmuiden",
        "Amsterdam",
        "camera",
        "europe",
        "UCIsPPwu9VefxZZ9tPYcQMmQ",
        place="IJmuiden port",
    ),
    LiveStream(
        "new-york-gwb",
        "New York",
        "camera",
        "americas",
        "UC0oDhoZzlJOQ7ABv3alrJEQ",
        place="George Washington Bridge",
    ),
    LiveStream(
        "new-york-brooklyn",
        "Brooklyn",
        "camera",
        "americas",
        "UC8YUfWLPR2Csqdb_ucphesA",
        place="Prospect Park",
    ),
    LiveStream(
        "niagara-falls",
        "Niagara Falls",
        "camera",
        "americas",
        "UCZiCPccfD2LhOznNHoqrbIQ",
        "PS46DvoJY-c",
        "The falls",
    ),
    LiveStream(
        "miami",
        "Miami",
        "camera",
        "americas",
        "UC5lNakfSdpqXCW214H5sBgQ",
        "GkBMgHwSIV0",
        "Skyline from MacArthur Causeway",
    ),
    LiveStream(
        "hong-kong-peak",
        "Hong Kong",
        "camera",
        "asia",
        "UCNcY1o1xGgTX_19w0cXes9g",
        place="Harbour from The Peak",
    ),
    LiveStream(
        "tokyo-shinjuku",
        "Tokyo",
        "camera",
        "asia",
        "UCjBhov7FGF0-Wc8Ayuycv3g",
        place="Shinjuku skyline",
    ),
    LiveStream(
        "seoul-han-river",
        "Seoul",
        "camera",
        "asia",
        "UCdBZr3OUnD6Cix_PY0Xxe2Q",
        "pYUCZ8ELKq0",
        "Han River skyline",
    ),
    LiveStream(
        "taipei-tamsui",
        "Taipei",
        "camera",
        "asia",
        "UCabUovFSOjIWaZN16BTniEQ",
        place="Tamsui river mouth",
    ),
    LiveStream(
        "singapore-marina-bay",
        "Singapore",
        "camera",
        "asia",
        "UCt6Eqg2fImBZvXnc14Gsmqg",
        place="Marina Bay skyline",
    ),
    LiveStream(
        "tokyo-haneda",
        "Tokyo Haneda",
        "camera",
        "asia",
        "UC_lRCs4pOVJl2Sv_Y4_Qsaw",
        place="Airport, Terminal 2",
    ),
    LiveStream(
        "iss",
        "International Space Station",
        "camera",
        "space",
        "UCLA_DiR1FfKNvjuUpBHmylQ",
        "M3HKLzjvKPc",
        "NASA's live view of Earth",
    ),
)

_BY_KEY: dict[str, LiveStream] = {s.key: s for s in STREAMS}


def by_key(key: str) -> LiveStream | None:
    return _BY_KEY.get(key)


def find(text: str) -> LiveStream | None:
    """A stream by key, or by a name or place the person said ("al jazeera",
    "tokyo", "the space station")."""
    wanted = " ".join(text.lower().replace("-", " ").split())
    if not wanted:
        return None
    hit = _BY_KEY.get(wanted.replace(" ", "-"))
    if hit is not None:
        return hit
    for stream in STREAMS:
        name = stream.name.lower()
        if wanted == name or (len(wanted) >= 3 and wanted in name) or name in wanted:
            return stream
    if "space station" in wanted or wanted == "space":
        return _BY_KEY["iss"]
    return None


def catalog_json() -> dict[str, Any]:
    return {
        "streams": [s.json() for s in STREAMS],
        "groups": [{"id": gid, "name": name} for gid, name in CAMERA_GROUPS],
        "news_groups": [{"id": gid, "name": name} for gid, name in NEWS_GROUPS],
    }


#: How long a resolved live video is trusted before the page is read again.
NOW_TTL_S = 20 * 60
#: How long a failed lookup waits before it is tried again.
NOW_RETRY_S = 2 * 60
PAGE_LIMIT = 3_000_000
_CANONICAL = re.compile(
    r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"'
)
#: Where the watch page's own details start. A live video says ``"isLive":true``
#: there, before its thumbnail; a scheduled one (a channel's live page can point
#: at a stream that has not started, even years later) does not.
_DETAILS = '"videoDetails":{"videoId":"'
#: One video on a channel's streams page.
_LOCKUP = '"lockupViewModel":'
_LOCKUP_ID = re.compile(r'"contentId":"([A-Za-z0-9_-]{11})"')
_LOCKUP_TITLE = re.compile(r'"lockupMetadataViewModel":\{"title":\{"content":"((?:[^"\\]|\\.)*)"')
_LIVE_BADGE = "THUMBNAIL_OVERLAY_BADGE_STYLE_LIVE"
_now: dict[str, tuple[float, str | None]] = {}
_POOL = HttpClientPool(
    timeout_s=8.0,
    client_kwargs={
        "follow_redirects": True,
        # The consent cookie answers the cookie wall a server outside the US
        # gets first; without it the page names no video.
        "headers": {"Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+1"},
    },
)


async def _fetch_page(url: str) -> str:
    async def fetch() -> str:
        async with _POOL.client().stream("GET", url) as res:
            res.raise_for_status()
            body = bytearray()
            async for chunk in res.aiter_bytes():
                body.extend(chunk)
                if len(body) > PAGE_LIMIT:
                    break
        return body.decode("utf-8", errors="replace")

    return await asyncio.wait_for(fetch(), timeout=12.0)


async def fetch_page(url: str) -> str:
    """Every page read goes through this name; tests replace it."""
    return await _fetch_page(url)


def live_video(page: str) -> str | None:
    """The video a channel's live page shows, if it is live now."""
    match = _CANONICAL.search(page)
    if match is None:
        return None
    video = match.group(1)
    at = page.find(_DETAILS + video)
    if at < 0:
        return None
    end = page.find('"thumbnail"', at)
    details = page[at : end if end > at else at + 4000]
    return video if '"isLive":true' in details else None


def hinted_video(page: str, hint: str) -> str | None:
    """The first live video on a channel's streams page whose title has ``hint``."""
    wanted = hint.casefold()
    for item in page.split(_LOCKUP)[1:]:
        if _LIVE_BADGE not in item:
            continue
        video = _LOCKUP_ID.search(item)
        title = _LOCKUP_TITLE.search(item)
        if video and title and wanted in title.group(1).casefold():
            return video.group(1)
    return None


async def now_playing(key: str, *, now: float | None = None) -> str | None:
    """The video a channel is streaming live now, or ``None``.

    A channel with a ``title_hint`` is read from its streams page and gives
    the live video whose title has the hint; any other channel gives the video
    its live page points at, when that video is live and not only scheduled.
    ``None`` means the channel is not live, or its page could not be read; the
    panel then falls back to the pinned video or the channel embed, or, for an
    occasional channel, says it is not live.
    """
    stream = _BY_KEY.get(key)
    if stream is None:
        return None
    clock = time.monotonic() if now is None else now
    cached = _now.get(key)
    if cached is not None and cached[0] > clock:
        return cached[1]
    tab = "streams" if stream.title_hint else "live"
    try:
        page = await fetch_page(f"https://www.youtube.com/channel/{stream.channel_id}/{tab}")
    except Exception as exc:  # noqa: BLE001 - any network failure means "not known now"
        log.info("live: could not read %s's %s page: %s", stream.key, tab, exc)
        _now[key] = (clock + NOW_RETRY_S, None)
        return None
    video = hinted_video(page, stream.title_hint) if stream.title_hint else live_video(page)
    _now[key] = (clock + (NOW_TTL_S if video else NOW_RETRY_S), video)
    return video
