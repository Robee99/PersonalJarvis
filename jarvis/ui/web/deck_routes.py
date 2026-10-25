"""REST API for the mission deck — the pictures it shows next to its numbers.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET  /api/deck/frame            → the last Screen-Context picture (bytes).
    GET  /api/deck/frame/meta       → whether one is held, and its shape.
    GET  /api/deck/cu-frame/{sha}   → one Computer-Use frame from the flight
                                      recorder, addressed by content hash.
    GET  /api/deck/vitals           → CPU, RAM, battery and GPU load now, plus
                                      the Windows power mode where it exists.
    GET  /api/deck/context          → how full the local model's context was
                                      on the newest agent turn (model_swap.py),
                                      plus the windows it can be restarted with.
    POST /api/deck/context          → restart the main local model with
                                      ``{"ctx": N}`` tokens, or as it is with
                                      ``{"ctx": null}``; Hermes follows.
    GET  /api/deck/news             → world headlines from free public RSS
                                      feeds, for the World screen's broadcast.
    GET    /api/deck/todos          → the owner's HUD to-do list ("Current
                                      tasks"): open first, then done.
    POST   /api/deck/todos          → add one ``{"title"}`` (201).
    PATCH  /api/deck/todos/{id}     → tick, untick or rename one.
    DELETE /api/deck/todos/{id}     → remove one.

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

Every picture response is ``Cache-Control: no-store``: a picture of the
screen must not outlive its budget in a browser cache either.

The to-do list is one small JSON file under the user data dir
(``hud/todos.json``), written atomically and guarded by one lock; it is the
only copy, so a corrupt file is moved aside before anything overwrites it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, StrictBool, StringConstraints

from jarvis.intel.news import WORLD_FEEDS, fetch_feeds
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


def _swap_settings(status: dict[str, Any]) -> dict[str, Any]:
    """What the context slider offers: the loaded window and the choices."""
    choices = [c for c in status.get("choices") or [] if isinstance(c, int)]
    window = status.get("window")
    return {
        "loaded": int(window) if isinstance(window, int) else None,
        "choices": choices,
        "vision_on_gpu": bool(status.get("vision_on_gpu")),
        "vision_gpu_max": status.get("vision_gpu_max_ctx"),
        "busy": any(n for n in (status.get("inflight") or {}).values() if isinstance(n, int)),
    }


@router.get("/context", summary="The local brain's context fill on its newest turn")
async def context() -> dict[str, Any]:
    """What the context meter shows: tokens in the newest agent turn, the
    window the model was started with, where Hermes compresses, and the
    windows the slider may restart the model with.

    The swapper reads the numbers from llama-server's own timings, so they are
    the model's count, not an estimate. No swapper (a cloud brain, another
    machine): ``available: false``, and the meter says so.
    """
    from jarvis.brain import swap_context  # noqa: PLC0415 - lazy (AP-26)

    status = await swap_context.read_status()
    if status is None:
        return {"available": False}
    settings = _swap_settings(status)
    ctx = status.get("context")
    if not isinstance(ctx, dict) or not isinstance(ctx.get("used"), int):
        return {"available": False, "active": status.get("active"), "settings": settings}
    window = int(settings["loaded"] or ctx.get("window") or 0)
    return {
        "available": True,
        "used": int(ctx["used"]),
        "window": window,
        "model": str(ctx.get("model") or ""),
        "at": ctx.get("at"),
        "compress_at": _compress_at(window),
        "settings": settings,
    }


class ContextChange(BaseModel):
    """``ctx``: the new window in tokens; ``None`` reloads the model as it is."""

    model_config = ConfigDict(extra="forbid")
    ctx: int | None = None


@router.post("/context", summary="Restart the local brain with another context window")
async def change_context(body: ContextChange) -> dict[str, Any]:
    """Restart the main local model (``ctx``: new window, ``None``: reload).

    The swapper waits for running turns, restarts the model and rolls back to
    the previous window when the new one does not load; the answer names the
    window that is loaded. Hermes's window and trim point are then set to it.
    """
    import asyncio  # noqa: PLC0415 - lazy (AP-26)

    from jarvis.brain import local_servers, swap_context  # noqa: PLC0415 - lazy (AP-26)

    status = await swap_context.read_status()
    if status is None:
        raise HTTPException(status_code=503, detail="The local model server is not running.")
    choices = _swap_settings(status)["choices"]
    if body.ctx is not None and body.ctx not in choices:
        raise HTTPException(status_code=422, detail=f"Pick one of {choices}.")
    answer = await swap_context.change(body.ctx)
    window = answer.get("window")
    result: dict[str, Any] = {
        "ok": bool(answer.get("ok")),
        "window": window if isinstance(window, int) else None,
        "vision_on_gpu": bool(answer.get("vision_on_gpu")),
        "seconds": answer.get("seconds"),
        "error": answer.get("error"),
        "hermes": None,
    }
    if not isinstance(window, int):
        return result
    from jarvis.cli_ctl.free_voice import _local_hermes_argv, _run  # noqa: PLC0415 - lazy (AP-26)

    try:
        hermes_config = await asyncio.to_thread(local_servers.read_hermes_config)
        if hermes_config:
            changed = await asyncio.to_thread(
                swap_context.sync_hermes,
                window,
                "",
                hermes_config=hermes_config,
                hermes_argv=_local_hermes_argv(),
                run=_run,
            )
            result["hermes"] = {"changed": changed}
    except RuntimeError as exc:
        log.warning("deck: Hermes kept its old window: %s", exc)
        result["hermes"] = {"changed": [], "error": str(exc)}
    return result


#: Free, keyless world-news feeds (the owner's rule: free sources only).
NEWS_FEEDS = WORLD_FEEDS
NEWS_TTL_S = 600.0
_news_cache: dict[str, Any] = {"at": 0.0, "body": None}


@router.get("/news", summary="World headlines from free public RSS feeds")
async def news() -> dict[str, Any]:
    """Headlines for the World screen, newest first, cached for ten minutes.

    A feed that fails is left out and named in ``failed``; nothing is made up.
    """
    import httpx  # noqa: PLC0415 - lazy (AP-26)

    cached = _news_cache["body"]
    if cached is not None and time.monotonic() - _news_cache["at"] < NEWS_TTL_S:
        return cached
    async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as http:
        by_source, failed = await fetch_feeds(NEWS_FEEDS, http)
    items = [item for got in by_source.values() for item in got]
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


# ---------------------------------------------------------------------------
# HUD to-do list
# ---------------------------------------------------------------------------

TODO_MAX_ITEMS = 200
TODO_TITLE_MAX = 200
_todo_lock = threading.Lock()


def todos_path() -> Path:
    """Where the to-do list lives; tests monkeypatch this function."""
    from jarvis.core.paths import user_data_dir  # noqa: PLC0415 - lazy (AP-26)

    return user_data_dir() / "hud" / "todos.json"


_Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TODO_TITLE_MAX)
]


class TodoCreate(BaseModel):
    """Body of ``POST /api/deck/todos``."""

    model_config = ConfigDict(extra="forbid")
    title: _Title


class TodoPatch(BaseModel):
    """Body of ``PATCH /api/deck/todos/{id}``; an omitted field is left as is."""

    model_config = ConfigDict(extra="forbid")
    done: StrictBool | None = None
    title: _Title | None = None


def _clean_todo(raw: object) -> dict[str, Any] | None:
    """One stored row in the API's shape, or ``None`` when it is malformed."""
    if not isinstance(raw, dict):
        return None
    todo_id, title, done = raw.get("id"), raw.get("title"), raw.get("done")
    created, done_ms = raw.get("created_ms"), raw.get("done_ms")
    if not isinstance(todo_id, str) or not todo_id or not isinstance(title, str):
        return None
    if not isinstance(done, bool) or type(created) is not int:
        return None
    if done_ms is not None and type(done_ms) is not int:
        return None
    return {
        "id": todo_id,
        "title": title,
        "done": done,
        "created_ms": created,
        "done_ms": done_ms if done else None,
    }


def _read_todos(path: Path) -> tuple[list[dict[str, Any]], bool]:
    """``(todos, corrupt)``. A missing file is an empty list; so is a corrupt
    one, with ``corrupt`` set so a writer can move it aside first."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:  # no list saved yet: an empty list is the truth
        return [], False
    try:
        data = json.loads(raw)
    except ValueError:
        log.warning("deck: to-do file %s is not valid JSON; reading it as empty", path)
        return [], True
    rows = data.get("todos") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        log.warning("deck: to-do file %s has no todos list; reading it as empty", path)
        return [], True
    todos = [todo for todo in map(_clean_todo, rows) if todo is not None]
    if len(todos) != len(rows):
        log.warning("deck: skipped %d malformed row(s) in %s", len(rows) - len(todos), path)
    return todos, False


def _write_todos(path: Path, todos: list[dict[str, Any]]) -> None:
    """Atomic write: a temp file in the same directory, then ``os.replace``.

    Local on purpose: ``config_writer``'s helper is private to ``jarvis.toml``
    and importing that module would pull its whole weight into this route.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "todos": todos}, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _ordered(todos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Open first (newest created first), then done (newest done first)."""
    open_ = sorted((t for t in todos if not t["done"]), key=lambda t: -t["created_ms"])
    done = sorted((t for t in todos if t["done"]), key=lambda t: -(t["done_ms"] or 0))
    return open_ + done


def _mutate_todos(change: Any) -> Any:
    """Read, apply ``change(todos)`` (which edits the list in place and
    returns the response), write — all under the one lock."""
    path = todos_path()
    with _todo_lock:
        todos, corrupt = _read_todos(path)
        result = change(todos)
        if corrupt:
            aside = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
            os.replace(path, aside)
            log.warning("deck: moved the unreadable to-do file aside to %s", aside)
        try:
            _write_todos(path, todos)
        except OSError as exc:
            log.error("deck: could not save the to-do list to %s: %s", path, exc)
            raise HTTPException(status_code=503, detail="todo_store_unwritable") from exc
        return result


def _find(todos: list[dict[str, Any]], todo_id: str) -> dict[str, Any]:
    for todo in todos:
        if todo["id"] == todo_id:
            return todo
    raise HTTPException(status_code=404, detail="todo_not_found")


def _now_ms() -> int:
    return int(time.time() * 1000)


@router.get("/todos", summary="The HUD to-do list")
def list_todos() -> dict[str, Any]:
    """Every to-do, open ones first (newest first), then done (newest done first)."""
    path = todos_path()
    with _todo_lock:
        todos, _corrupt = _read_todos(path)
    return {"todos": _ordered(todos)}


@router.post("/todos", status_code=201, summary="Add a to-do")
def create_todo(body: TodoCreate) -> dict[str, Any]:
    """Add one open to-do. 409 once the list holds ``TODO_MAX_ITEMS``."""

    def change(todos: list[dict[str, Any]]) -> dict[str, Any]:
        if len(todos) >= TODO_MAX_ITEMS:
            raise HTTPException(
                status_code=409,
                detail=f"The to-do list is full ({TODO_MAX_ITEMS} items); delete some first.",
            )
        todo = {
            "id": uuid.uuid4().hex,
            "title": body.title,
            "done": False,
            "created_ms": _now_ms(),
            "done_ms": None,
        }
        todos.append(todo)
        return {"todo": todo}

    return _mutate_todos(change)


@router.patch("/todos/{todo_id}", summary="Tick, untick or rename a to-do")
def update_todo(todo_id: str, body: TodoPatch) -> dict[str, Any]:
    """``done: true`` stamps ``done_ms``; ``done: false`` clears it."""

    def change(todos: list[dict[str, Any]]) -> dict[str, Any]:
        todo = _find(todos, todo_id)
        if body.title is not None:
            todo["title"] = body.title
        if body.done is True and not todo["done"]:
            todo["done"], todo["done_ms"] = True, _now_ms()
        elif body.done is False:
            todo["done"], todo["done_ms"] = False, None
        return {"todo": dict(todo)}

    return _mutate_todos(change)


@router.delete("/todos/{todo_id}", summary="Delete a to-do")
def delete_todo(todo_id: str) -> dict[str, Any]:
    """Remove one to-do; 404 when no such id exists."""

    def change(todos: list[dict[str, Any]]) -> dict[str, Any]:
        todos.remove(_find(todos, todo_id))
        return {"ok": True}

    return _mutate_todos(change)


__all__ = ["router", "CU_BLOB_DIR", "todos_path"]
