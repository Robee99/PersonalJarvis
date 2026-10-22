"""The shared incident model and the guards every upstream value passes.

An :class:`IntelEvent` is built only from what its source gave. A missing
number is ``None``, never 0; a position the source did not give (or gave only
as a country centroid) is ``None`` and the event stays in the list without a
place on the globe. Text from a source is untrusted: control characters are
removed, whitespace collapsed and length capped, and it is never rendered as
markup or followed as an instruction. Only ``http(s)`` links survive.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

TITLE_MAX = 160
SUMMARY_MAX = 400
PLACE_MAX = 120
URL_MAX = 600
SOURCES_MAX = 8


class Evidence(StrEnum):
    """What an event rests on, from strongest to weakest."""

    #: An instrument network measured it (a seismometer, a hurricane centre's
    #: observed position).
    MEASURED = "measured"
    #: A public agency published it as an alert or curated event (GDACS,
    #: NASA EONET). GDACS impact levels come from an automated model.
    AGENCY = "agency"
    #: Machine-coded from news articles (GDELT). A claim made in the media,
    #: not a confirmed event.
    MEDIA = "media"


class Kind(StrEnum):
    EARTHQUAKE = "earthquake"
    CYCLONE = "cyclone"
    FLOOD = "flood"
    VOLCANO = "volcano"
    WILDFIRE = "wildfire"
    DROUGHT = "drought"
    STORM = "storm"
    LANDSLIDE = "landslide"
    ICE = "ice"
    DUST = "dust"
    PROTEST = "protest"
    VIOLENCE = "violence"
    OTHER = "other"


#: Which broad category each kind belongs to (the panel's first filter).
CATEGORY: dict[Kind, str] = {
    Kind.PROTEST: "unrest",
    Kind.VIOLENCE: "unrest",
}


def category_of(kind: Kind) -> str:
    return CATEGORY.get(kind, "hazard")


@dataclass(frozen=True, slots=True)
class SourceLink:
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class IntelEvent:
    """One incident, as one source reported it."""

    #: ``<source>:<native id>``; stable across refreshes of that source.
    id: str
    source: str
    kind: Kind
    title: str
    evidence: Evidence
    #: When it happened (or began), unix ms.
    occurred_at: int
    lat: float | None = None
    lon: float | None = None
    place: str | None = None
    country: str | None = None
    summary: str | None = None
    #: When the source last changed it, unix ms; ``None`` when not given.
    updated_at: int | None = None
    #: 0 (minor) .. 4 (extreme), ONLY from a scale the source gave
    #: (magnitude, alert level, wind speed); ``None`` otherwise.
    severity: int | None = None
    #: The source's own wording of that scale ("M 6.1", "GDACS orange").
    severity_label: str | None = None
    #: The severity or the classification came from an automated model
    #: (GDACS impact model, GDELT machine coding) rather than a person or an
    #: instrument.
    automated: bool = False
    links: tuple[SourceLink, ...] = ()
    #: Source-specific numbers worth keeping (magnitude, depth, wind, mentions).
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def category(self) -> str:
        return category_of(self.kind)

    @property
    def has_position(self) -> bool:
        return self.lat is not None and self.lon is not None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "kind": self.kind.value,
            "category": self.category,
            "title": self.title,
            "summary": self.summary,
            "evidence": self.evidence.value,
            "automated": self.automated,
            "occurred_at": self.occurred_at,
            "updated_at": self.updated_at,
            "lat": self.lat,
            "lon": self.lon,
            "place": self.place,
            "country": self.country,
            "severity": self.severity,
            "severity_label": self.severity_label,
            "links": [{"name": link.name, "url": link.url} for link in self.links],
            "attrs": dict(self.attrs),
        }


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")
_SPACE = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]{0,200}>")


def clean_text(value: Any, limit: int) -> str | None:
    """Plain, single-line, bounded text, or ``None`` when nothing is left.

    Tags are dropped (a source's HTML description stays text), as are control
    and bidirectional-override characters that could disguise what a title
    says.
    """
    if not isinstance(value, str):
        return None
    text = _SPACE.sub(" ", _CONTROL.sub("", _TAG.sub(" ", value))).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def safe_url(value: Any) -> str | None:
    """An absolute ``http(s)`` URL without credentials, or ``None``."""
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url or len(url) > URL_MAX or any(ch.isspace() for ch in url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:  # a malformed URL is dropped, like any other bad value
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None
    return url


def number(value: Any) -> float | None:
    """A finite float, or ``None`` (bools and text that is not a number too)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):  # not a number: unknown, never invented
        return None
    return out if math.isfinite(out) else None


def position(lat: Any, lon: Any) -> tuple[float, float] | None:
    """A valid WGS84 point, or ``None``. (0, 0) is refused: in these feeds it
    means "no position", not the Gulf of Guinea."""
    la, lo = number(lat), number(lon)
    if la is None or lo is None or not (-90 <= la <= 90) or not (-180 <= lo <= 180):
        return None
    if la == 0 and lo == 0:
        return None
    return round(la, 4), round(lo, 4)


def link(name: str, url: Any) -> SourceLink | None:
    checked = safe_url(url)
    return SourceLink(name=name, url=checked) if checked else None


def links(*items: SourceLink | None) -> tuple[SourceLink, ...]:
    seen: set[str] = set()
    out: list[SourceLink] = []
    for item in items:
        if item is None or item.url in seen:
            continue
        seen.add(item.url)
        out.append(item)
    return tuple(out[:SOURCES_MAX])
