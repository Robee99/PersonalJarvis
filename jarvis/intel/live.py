"""Live video for the World screen: news channels and public city cameras.

Every stream is a public YouTube live stream played in YouTube's own embedded
player, so JARVIS fetches nothing and needs no key. A stream is named by its
channel; YouTube's ``live_stream?channel=`` embed then plays whatever that
channel has live. A channel that runs several live streams at once does not
resolve that way, so its entry pins one video id, checked live and embeddable
on 2026-10-19. A pinned stream that ends shows YouTube's "unavailable" card;
the panel says so and links the channel's live page.

Cameras are fixed views of skylines, landmarks, ports, bridges and airports
run by their own channels. Re-broadcasts of other people's cameras are left
out, so what the panel calls a camera is that camera.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

StreamKind = Literal["news", "camera"]


@dataclass(frozen=True, slots=True)
class LiveStream:
    key: str
    name: str
    kind: StreamKind
    #: Camera group ("middle-east", "europe", "americas", "asia", "space");
    #: empty for news.
    group: str
    channel_id: str
    #: Set when the channel's live page does not resolve to one stream.
    video_id: str = ""
    #: Where a camera looks, in a few words.
    place: str = ""
    #: A shorter name for a channel button, when the name is long.
    short: str = ""

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
        }


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
        "",
        "UCNye-wNBqNL5ZzHSJj3l8Bg",
        short="Al Jazeera",
    ),
    LiveStream("sky", "Sky News", "news", "", "UCoMdktPbSTixAyNGwb-UYkQ"),
    LiveStream(
        "france24", "France 24 English", "news", "", "UCQfwfsi5VrQ8yKZ-UWmAEFg", short="France 24"
    ),
    LiveStream(
        "bloomberg",
        "Bloomberg Television",
        "news",
        "",
        "UCIALMKvObZNtJ6AmdCLP7Lg",
        short="Bloomberg",
    ),
    LiveStream("nbc", "NBC News NOW", "news", "", "UCeY0bbntWzzVIaj2z3QigXg", short="NBC News"),
    LiveStream("trt", "TRT World", "news", "", "UC7fWeaHhqgM4Ry-RMpM2YYw"),
    LiveStream("cna", "CNA", "news", "", "UC83jt4dlz1Gjl58fzQrrKZg"),
    LiveStream(
        "euronews",
        "Euronews English",
        "news",
        "",
        "UCSrZ3UV4jOidv8ppoVuvW9Q",
        "pykpO5kQJ98",
        short="Euronews",
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
    }
