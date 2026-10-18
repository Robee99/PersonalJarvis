"""REST API for the mission deck — the pictures it shows next to its numbers.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET  /api/deck/frame            → the last Screen-Context picture (bytes).
    GET  /api/deck/frame/meta       → whether one is held, and its shape.
    GET  /api/deck/cu-frame/{sha}   → one Computer-Use frame from the flight
                                      recorder, addressed by content hash.
    GET  /api/deck/vitals           → CPU, RAM, battery and GPU load now, plus
                                      the Windows power mode where it exists.
    GET  /api/deck/context          → how full the local model's context was
                                      on the newest agent turn (model_swap.py).
    GET  /api/deck/news             → world headlines from free public RSS
                                      feeds, for the World screen's broadcast.

Two producers, two shapes, one reason: the deck shows the user what Jarvis
just looked at.

* The Screen-Context picture comes from
  :mod:`jarvis.screen_context.last_frame` — at most one frame, in memory,
  gone after ``[screen_context].deck_preview_s`` seconds. The route never
  captures; it only serves what the service already produced for a capture
  the user asked for.
* Computer-Use frames are ALREADY on disk: the harness stores every
  observation under ``data/flight_recorder/blobs/<sha256>.<ext>`` for replay,
  and ``ObservationCaptured`` carries that hash. Serving by hash — validated
  as 64 hex characters, joined onto the fixed blob directory — is how the
  route stays traversal-proof without ever accepting a path.

Every response is ``Cache-Control: no-store``: a picture of the screen must
not outlive its budget in a browser cache either.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response

from jarvis.screen_context.last_frame import get_last_frame_mirror

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/deck", tags=["deck"])

#: The Computer-Use harness writes here (``jarvis/cu/engine.py``) — the same
#: constant, not a lookup, so a moved recorder shows up as a test failure
#: rather than a route that silently serves nothing.
CU_BLOB_DIR = Path("data") / "flight_recorder" / "blobs"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NO_STORE = {"Cache-Control": "no-store"}
_CU_EXTENSIONS = (".jpg", ".png")
_MIME_BY_SUFFIX = {".jpg": "image/jpeg", ".png": "image/png"}


@router.get("/frame/meta", summary="Whether a Screen-Context picture is held")
def frame_meta() -> dict[str, Any]:
    """Shape of the held picture, or ``available: false``.

    Cheap on purpose — the deck polls this after a ``ScreenCaptureCompleted``
    event and only fetches bytes when ``seq`` moved.
    """
    frame = get_last_frame_mirror().get()
    if frame is None:
        return {"available": False, "seq": 0}
    return {
        "available": True,
        "seq": frame.seq,
        "width": frame.width,
        "height": frame.height,
        "mime": frame.mime,
        "source": frame.source,
        "target_label": frame.target_label,
        "captured_at_ns": frame.captured_at_ns,
        "expires_at_ns": frame.expires_at_ns,
    }


@router.get("/frame", summary="The last Screen-Context picture")
def frame() -> Response:
    """The held picture as bytes, or 404 when none is held (or it expired)."""
    held = get_last_frame_mirror().get()
    if held is None:
        raise HTTPException(status_code=404, detail="no_frame")
    return Response(
        content=held.image,
        media_type=held.mime,
        headers={
            **_NO_STORE,
            "X-Frame-Seq": str(held.seq),
            "X-Frame-Width": str(held.width),
            "X-Frame-Height": str(held.height),
            "X-Frame-Source": held.source,
        },
    )


@router.get("/cu-frame/{sha}", summary="One Computer-Use frame, by content hash")
def cu_frame(sha: str) -> Response:
    """A recorded Computer-Use observation from the flight recorder.

    404 covers both "never recorded" and "already pruned by the retention
    sweep" — the deck treats them the same (show nothing, keep going).
    """
    if not _SHA256_RE.fullmatch(sha):
        # A hash is the ONLY accepted key. Anything else — a filename, a
        # path, a shorter digest — is refused before it touches the filesystem.
        raise HTTPException(status_code=400, detail="invalid_hash")
    for ext in _CU_EXTENSIONS:
        candidate = CU_BLOB_DIR / f"{sha}{ext}"
        if candidate.is_file():
            try:
                data = candidate.read_bytes()
            except OSError:
                log.debug("deck: cu frame %s unreadable", sha, exc_info=True)
                break
            return Response(
                content=data,
                media_type=_MIME_BY_SUFFIX[ext],
                headers=_NO_STORE,
            )
    raise HTTPException(status_code=404, detail="no_frame")


@router.get("/vitals", summary="CPU, RAM, battery and GPU load right now")
def vitals() -> dict[str, Any]:
    """One live reading for the deck's vitals card; sync, so it runs in the
    threadpool while the CPU sample and ``nvidia-smi`` take their time."""
    from jarvis.hardware.detection import live_vitals  # noqa: PLC0415 - lazy (AP-26)

    reading: dict[str, Any] = dict(live_vitals())
    reading["power_mode"] = _power_mode()
    return reading


#: The model swapper's fronts all answer ``/swap/status`` with the same state;
#: 11440 is Qwen3-VL's, the brain's default (scripts/windows/model_swap.py).
SWAP_STATUS_URL = "http://127.0.0.1:11440/swap/status"


@router.get("/context", summary="The local brain's context fill on its newest turn")
async def context() -> dict[str, Any]:
    """What the context meter shows: tokens in the newest agent turn, the
    window the model was started with, and where Hermes compresses.

    The swapper reads the numbers from llama-server's own timings, so they are
    the model's count, not an estimate. No swapper (a cloud brain, another
    machine): ``available: false``, and the meter says so.
    """
    import httpx  # noqa: PLC0415 - lazy (AP-26)

    try:
        async with httpx.AsyncClient(trust_env=False, timeout=1.5) as http:
            res = await http.get(SWAP_STATUS_URL)
            res.raise_for_status()
            status = res.json()
    except (httpx.HTTPError, ValueError):
        return {"available": False}
    ctx = status.get("context") if isinstance(status, dict) else None
    if not isinstance(ctx, dict) or not isinstance(ctx.get("used"), int):
        return {"available": False, "active": status.get("active")}
    window = int(ctx.get("window") or 0)
    return {
        "available": True,
        "used": int(ctx["used"]),
        "window": window,
        "model": str(ctx.get("model") or ""),
        "at": ctx.get("at"),
        "compress_at": _compress_at(window),
    }


#: Free, keyless world-news feeds (the owner's rule: free sources only).
NEWS_FEEDS: tuple[tuple[str, str], ...] = (
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("NPR World", "https://feeds.npr.org/1004/rss.xml"),
)
NEWS_TTL_S = 600.0
_NEWS_MAX_BYTES = 2_000_000
_news_cache: dict[str, Any] = {"at": 0.0, "body": None}


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
        return []
    items: list[dict[str, Any]] = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        if not title or not link.startswith(("https://", "http://")):
            continue
        published: float | None = None
        stamp = node.findtext("pubDate")
        if stamp:
            try:
                published = parsedate_to_datetime(stamp).timestamp()
            except (TypeError, ValueError):
                published = None
        items.append({"title": title, "link": link, "source": source, "published": published})
        if len(items) >= limit:
            break
    return items


@router.get("/news", summary="World headlines from free public RSS feeds")
async def news() -> dict[str, Any]:
    """Headlines for the World screen, newest first, cached for ten minutes.

    A feed that fails is left out and named in ``failed``; nothing is made up.
    """
    import httpx  # noqa: PLC0415 - lazy (AP-26)

    cached = _news_cache["body"]
    if cached is not None and time.monotonic() - _news_cache["at"] < NEWS_TTL_S:
        return cached
    items: list[dict[str, Any]] = []
    failed: list[str] = []
    async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as http:
        for source, url in NEWS_FEEDS:
            try:
                res = await http.get(url, headers={"User-Agent": "Jarvis-HUD/1.0"})
                res.raise_for_status()
                got = parse_rss(source, res.content[:_NEWS_MAX_BYTES])
            except httpx.HTTPError:
                got = []
            if got:
                items += got
            else:
                failed.append(source)
    items.sort(key=lambda i: i["published"] or 0, reverse=True)
    body = {
        "items": items[:40],
        "sources": [name for name, _ in NEWS_FEEDS],
        "failed": failed,
        "fetched_at": time.time(),
    }
    if items:
        _news_cache.update(at=time.monotonic(), body=body)
    return body


def _compress_at(window: int) -> int | None:
    """Where Hermes compresses the conversation, from its own config."""
    from jarvis.brain.local_servers import read_hermes_config  # noqa: PLC0415 - lazy (AP-26)

    try:
        compression = read_hermes_config().get("compression") or {}
    except Exception:  # noqa: BLE001 - no Hermes: no mark on the meter
        log.debug("deck: no Hermes compression setting", exc_info=True)
        return None
    if not isinstance(compression, dict) or compression.get("enabled") is False:
        return None
    tokens = compression.get("threshold_tokens")
    if isinstance(tokens, int) and tokens > 0:
        return min(tokens, window) if window else tokens
    ratio = compression.get("threshold")
    if isinstance(ratio, (int, float)) and 0 < ratio < 1 and window:
        return int(window * ratio)
    return None


def _power_mode() -> str | None:
    """The Windows power mode slider, or ``None`` where there is none."""
    from jarvis.platform.laptop_power import (  # noqa: PLC0415 - lazy (AP-26)
        LaptopPowerUnavailable,
        WindowsPowerMode,
    )

    try:
        return WindowsPowerMode().current()
    except (LaptopPowerUnavailable, OSError):
        log.debug("deck: no power mode reading", exc_info=True)
        return None


__all__ = ["router", "CU_BLOB_DIR"]
