"""Fetches the sources when someone is looking, and keeps their status honest.

There is no background poller: a refresh happens when a reader asks (the
World screen polls while it is open, an agent tool asks when it runs) and a
source is due. Each source has its own interval and lock, so concurrent
readers share one upstream call, and a failing source backs off (doubling, up
to half an hour) while the others carry on. A source that fails keeps its last
good events, marked stale; one that never answered is ``unavailable``.

GDELT publishes a new event file every 15 minutes. The service keeps a rolling
24-hour window of those files and fetches only the ones it does not have yet,
a few per refresh, newest first.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from jarvis.core.http_pool import HttpClientPool

from . import parsers
from .model import IntelEvent
from .store import IntelStore

log = logging.getLogger(__name__)

USER_AGENT = "Jarvis-Intel/1.0 (+https://github.com/Robee99/PersonalJarvis)"
FETCH_TIMEOUT_S = 8.0
FETCH_DEADLINE_S = 15.0
MAX_JSON_BYTES = 4_000_000
MAX_ZIP_BYTES = 3_000_000
MAX_UNZIPPED_BYTES = 30_000_000
MAX_BACKOFF_S = 1800.0
MIN_RETRY_S = 60.0

USGS_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson"
GDACS_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/EVENTS4APP"
EONET_URL = "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&days=30"
NHC_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"
GDELT_LAST_URL = "https://data.gdeltproject.org/gdeltv2/lastupdate.txt"
GDELT_FILE_URL = "https://data.gdeltproject.org/gdeltv2/{stamp}.export.CSV.zip"
GDELT_STEP = timedelta(minutes=15)
#: Files in the rolling window (24 h) and how many to fetch per refresh.
GDELT_WINDOW_FILES = 96
GDELT_FILES_PER_REFRESH = 6


class UpstreamError(RuntimeError):
    """An upstream answered, but not with something usable."""


_POOL = HttpClientPool(
    timeout_s=FETCH_TIMEOUT_S,
    client_kwargs={"follow_redirects": True, "headers": {"User-Agent": USER_AGENT}},
)


async def _fetch_bytes(url: str, limit: int) -> bytes:
    async def fetch() -> bytes:
        async with _POOL.client().stream("GET", url) as res:
            res.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            async for chunk in res.aiter_bytes():
                size += len(chunk)
                if size > limit:
                    raise UpstreamError(f"{url} sent more than {limit} bytes")
                chunks.append(chunk)
        return b"".join(chunks)

    return await asyncio.wait_for(fetch(), timeout=FETCH_DEADLINE_S)


async def fetch_bytes(url: str, limit: int) -> bytes:
    """Every download goes through this name; tests replace it."""
    return await _fetch_bytes(url, limit)


async def fetch_json(url: str) -> Any:
    return json.loads(await fetch_bytes(url, MAX_JSON_BYTES))


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceInfo:
    id: str
    name: str
    #: What the reader is told about the data's origin and terms.
    attribution: str
    home_url: str
    terms_url: str
    interval_s: float


SOURCES: tuple[SourceInfo, ...] = (
    SourceInfo(
        "usgs",
        "USGS earthquakes",
        "U.S. Geological Survey, Earthquake Hazards Program (public domain).",
        "https://earthquake.usgs.gov/earthquakes/feed/",
        "https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits",
        300.0,
    ),
    SourceInfo(
        "gdacs",
        "GDACS disaster alerts",
        "Global Disaster Alert and Coordination System (European Commission JRC, UN OCHA). "
        "Alert levels come from automated impact models.",
        "https://www.gdacs.org/",
        "https://www.gdacs.org/About/termofuse.aspx",
        900.0,
    ),
    SourceInfo(
        "eonet",
        "NASA EONET natural events",
        "NASA Earth Observatory Natural Event Tracker; each event links its own source.",
        "https://eonet.gsfc.nasa.gov/",
        "https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance",
        1800.0,
    ),
    SourceInfo(
        "nhc",
        "NOAA hurricane centre",
        "NOAA National Hurricane Center (public domain).",
        "https://www.nhc.noaa.gov/",
        "https://www.weather.gov/disclaimer",
        1800.0,
    ),
    SourceInfo(
        "gdelt",
        "GDELT news events",
        "The GDELT Project (gdeltproject.org): events machine-coded from news reports. "
        "Media claims, not confirmed events.",
        "https://www.gdeltproject.org/",
        "https://www.gdeltproject.org/about.html#termsofuse",
        900.0,
    ),
)
SOURCE_BY_ID = {source.id: source for source in SOURCES}


@dataclass(slots=True)
class SourceState:
    #: never | ok | stale | unavailable
    status: str = "never"
    fetched_at: int | None = None
    error: str | None = None
    failures: int = 0
    count: int = 0
    next_due: float = 0.0
    lock: asyncio.Lock | None = None
    lock_loop: Any = None

    def guard(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self.lock is None or self.lock_loop is not loop:
            self.lock, self.lock_loop = asyncio.Lock(), loop
        return self.lock


def _monotonic() -> float:
    return time.monotonic()


def _wall_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class GdeltWindow:
    """The rolling window of parsed GDELT export files, by file stamp."""

    files: dict[str, list[parsers.GdeltRow]] = field(default_factory=dict)

    def stamps_wanted(self, newest: str) -> list[str]:
        moment = datetime.strptime(newest, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        return [
            (moment - GDELT_STEP * i).strftime("%Y%m%d%H%M%S") for i in range(GDELT_WINDOW_FILES)
        ]

    def keep_only(self, wanted: list[str]) -> None:
        keep = set(wanted)
        for stamp in [s for s in self.files if s not in keep]:
            del self.files[stamp]

    def rows(self) -> list[parsers.GdeltRow]:
        return [row for rows in self.files.values() for row in rows]


def _unzip_rows(blob: bytes) -> list[parsers.GdeltRow]:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = archive.namelist()
        if not names:
            return []
        info = archive.getinfo(names[0])
        if info.file_size > MAX_UNZIPPED_BYTES:
            raise UpstreamError("GDELT file is larger than expected")
        text = archive.read(names[0]).decode("utf-8", errors="replace")
    return parsers.parse_gdelt_rows(text.splitlines())


class IntelService:
    def __init__(self, store: IntelStore | None = None) -> None:
        self.store = store or IntelStore()
        self.states: dict[str, SourceState] = {s.id: SourceState() for s in SOURCES}
        self.gdelt = GdeltWindow()
        self._task: asyncio.Task[None] | None = None
        self._task_loop: Any = None
        self._fetchers: dict[str, Callable[[], Awaitable[list[IntelEvent]]]] = {
            "usgs": self._usgs,
            "gdacs": self._gdacs,
            "eonet": self._eonet,
            "nhc": self._nhc,
            "gdelt": self._gdelt,
        }

    # -- sources -----------------------------------------------------------

    async def _usgs(self) -> list[IntelEvent]:
        return parsers.parse_usgs(await fetch_json(USGS_URL))

    async def _gdacs(self) -> list[IntelEvent]:
        return parsers.parse_gdacs(await fetch_json(GDACS_URL))

    async def _eonet(self) -> list[IntelEvent]:
        return parsers.parse_eonet(await fetch_json(EONET_URL))

    async def _nhc(self) -> list[IntelEvent]:
        return parsers.parse_nhc(await fetch_json(NHC_URL))

    async def _gdelt(self) -> list[IntelEvent]:
        listing = (await fetch_bytes(GDELT_LAST_URL, 4096)).decode("ascii", errors="replace")
        newest = None
        for line in listing.splitlines():
            url = line.split(" ")[-1].strip()
            if url.endswith(".export.CSV.zip"):
                newest = url.rsplit("/", 1)[-1].split(".", 1)[0]
        if not newest or len(newest) != 14 or not newest.isdigit():
            raise UpstreamError("GDELT lastupdate.txt names no export file")
        wanted = self.gdelt.stamps_wanted(newest)
        self.gdelt.keep_only(wanted)
        missing = [stamp for stamp in wanted if stamp not in self.gdelt.files]
        import httpx  # noqa: PLC0415 - lazy (AP-26)

        for stamp in missing[:GDELT_FILES_PER_REFRESH]:
            try:
                blob = await fetch_bytes(GDELT_FILE_URL.format(stamp=stamp), MAX_ZIP_BYTES)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    # GDELT skips a slot now and then; remember the gap.
                    self.gdelt.files[stamp] = []
                    continue
                raise
            self.gdelt.files[stamp] = await asyncio.to_thread(_unzip_rows, blob)
        return parsers.cluster_gdelt(self.gdelt.rows())

    # -- refresh -----------------------------------------------------------

    async def refresh_source(self, source_id: str) -> None:
        state = self.states[source_id]
        info = SOURCE_BY_ID[source_id]
        async with state.guard():
            now = _monotonic()
            if now < state.next_due:
                return
            import httpx  # noqa: PLC0415 - lazy (AP-26)

            try:
                events = await self._fetchers[source_id]()
            except (
                httpx.HTTPError,
                UpstreamError,
                ValueError,
                TypeError,
                TimeoutError,
                zipfile.BadZipFile,
            ) as exc:
                self._failed(source_id, state, info, exc)
                return
            except Exception as exc:  # a parser bug degrades this one source; logged with its trace
                log.warning("intel: %s refresh raised unexpectedly", source_id, exc_info=True)
                self._failed(source_id, state, info, exc)
                return
            self.store.replace_source(source_id, events)
            state.status, state.error, state.failures = "ok", None, 0
            state.fetched_at = int(time.time() * 1000)
            state.count = len(self.store.ids_for(source_id))
            # GDELT catches up on its window a few files at a time.
            behind = source_id == "gdelt" and len(self.gdelt.files) < GDELT_WINDOW_FILES
            state.next_due = _monotonic() + (MIN_RETRY_S if behind else info.interval_s)

    @staticmethod
    def _failed(source_id: str, state: SourceState, info: SourceInfo, exc: BaseException) -> None:
        state.failures += 1
        backoff = min(MAX_BACKOFF_S, max(MIN_RETRY_S, info.interval_s * 2 ** (state.failures - 1)))
        state.next_due = _monotonic() + backoff
        state.error = f"{type(exc).__name__}: {str(exc)[:160]}"
        state.status = "stale" if state.fetched_at is not None else "unavailable"
        log.info("intel: %s refresh failed (%s); retry in %.0fs", source_id, state.error, backoff)

    def due(self) -> list[str]:
        now = _monotonic()
        return [sid for sid, state in self.states.items() if now >= state.next_due]

    async def _refresh_due(self) -> None:
        await asyncio.gather(*(self.refresh_source(sid) for sid in self.due()))

    async def refresh(self, wait_s: float) -> None:
        """Start a refresh of the due sources; wait for it up to ``wait_s``.

        One refresh runs at a time. A reader that already has data asks with a
        short wait and gets the current set while the refresh finishes.
        """
        loop = asyncio.get_running_loop()
        task = self._task if self._task_loop is loop else None
        if (task is None or task.done()) and self.due():
            task = loop.create_task(self._refresh_due())
            self._task, self._task_loop = task, loop
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=wait_s)
        except TimeoutError:
            return  # still running: the reader gets what is there now

    # -- reading -----------------------------------------------------------

    def is_stale(self, source_id: str) -> bool:
        return self.states[source_id].status == "stale"

    def record(self, event: IntelEvent) -> dict[str, Any]:
        return self.store.record(event, stale=self.is_stale(event.source))

    def sources_json(self) -> list[dict[str, Any]]:
        out = []
        for info in SOURCES:
            state = self.states[info.id]
            out.append(
                {
                    "id": info.id,
                    "name": info.name,
                    "attribution": info.attribution,
                    "home_url": info.home_url,
                    "terms_url": info.terms_url,
                    "status": state.status,
                    "fetched_at": state.fetched_at,
                    "count": state.count,
                    "error": state.error,
                    "refresh_s": info.interval_s,
                }
            )
        return out


_SERVICE: IntelService | None = None


def shared_service() -> IntelService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = IntelService()
    return _SERVICE


def reset_service() -> None:
    """Forget every event and state. Tests only."""
    global _SERVICE
    _SERVICE = None
    _POOL.forget()
