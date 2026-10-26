"""Jarvis stands in for the Kept desktop app, so chats sync straight into memory.

Kept (github.com/egroup-labs/kept, MIT) is a Chrome extension plus a desktop
app. The extension reads every ChatGPT, Claude, Gemini, Grok and Kimi chat
through the owner's own signed-in sessions (no API keys, no password) and
posts each one as Markdown to the app on ``localhost:18241``. A second app
with its own vault, search and graph would be a parallel memory, so Jarvis
answers on that port itself and writes each chat into the wiki through
:class:`~jarvis.memory.wiki.importer.ChatSaver`: sign in once in Chrome, and
the chats are in memory, updated as they grow.

The extension is bundled in ``jarvis/assets/kept-extension`` unchanged. It
connects by opening ``/connect``, which carries a token in a ``<meta>`` tag;
every other call must bring that token. Only ``localhost`` and ``127.0.0.1``
are served (no DNS rebinding), the extension cannot choose where a file goes,
and a chat that looks like it holds a key or token is refused.

If the real Kept app already owns the port, Jarvis leaves it alone.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import re
import secrets
import socket
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any, Final

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from jarvis.memory.wiki.import_formats import CHAT_PLATFORMS, _clean_id
from jarvis.memory.wiki.importer import ChatSaver, ImportRefused

log = logging.getLogger(__name__)

KEPT_PORT: Final[int] = 18241
TOKEN_FILE: Final[str] = "kept_bridge_token"  # noqa: S105 — a file name, not a secret
#: One chat's Markdown above this is refused (a whole book pasted in a chat).
MAX_MARKDOWN_CHARS: Final[int] = 8_000_000
_HOSTS: Final[frozenset[str]] = frozenset({f"localhost:{KEPT_PORT}", f"127.0.0.1:{KEPT_PORT}"})
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def load_token(data_dir: Path) -> str:
    """The connection token, made once and kept next to the wiki index."""
    path = data_dir / TOKEN_FILE
    try:
        token = path.read_text("utf-8").strip()
        if len(token) >= 32:
            return token
    except OSError:  # first start: a new token is made below
        pass
    token = secrets.token_urlsafe(32)
    data_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(token, "utf-8")
    return token


_CONNECT_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="kept-token" content="{token}">
<title>Jarvis memory: connect Kept</title>
<style>body{{font:15px system-ui;background:#0b0f14;color:#dbe7f3;display:grid;place-items:center;
height:100vh;margin:0}}main{{max-width:30rem}}.status-tag{{padding:.2rem .6rem;border-radius:1rem;
background:#1f2a37}}.connected{{background:#14532d}}.not-found{{background:#7f1d1d}}</style></head>
<body><main><h1>Jarvis memory</h1>
<p>Kept: <span id="kept-status-tag" class="status-tag">waiting for the extension</span></p>
<p id="kept-connect-detail">Open this page in the Chrome where the Kept extension is installed.</p>
<div id="kept-install-section"><p>Load <code>jarvis/assets/kept-extension</code> in
chrome://extensions (Developer mode, Load unpacked), then click Connect in its popup.</p></div>
</main></body></html>"""


def _authorized(request: Request, token: str) -> bool:
    given = request.headers.get("authorization", "")
    return given.startswith("Bearer ") and hmac.compare_digest(given[7:].strip(), token)


def build_app(saver: ChatSaver, token: str) -> FastAPI:
    """The handful of Kept app routes the extension calls."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    # The first ping after Jarvis starts asks for a sync, so chats made while
    # Jarvis was off arrive at once; the extension skips the unchanged ones.
    pending_sync = {"first": True}

    @app.middleware("http")
    async def local_only(request: Request, call_next: Any) -> Any:
        if request.headers.get("host", "") not in _HOSTS:
            return JSONResponse({"error": "local only"}, status_code=403)
        return await call_next(request)

    @app.get("/api/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok", "app": "jarvis"}

    @app.get("/connect", response_class=HTMLResponse)
    def connect() -> str:
        return _CONNECT_PAGE.format(token=escape(token, quote=True))

    @app.post("/api/extension_ping")
    def extension_ping(request: Request) -> dict[str, bool]:
        if not _authorized(request, token):
            raise HTTPException(status_code=401, detail="connect again")
        requested, pending_sync["first"] = pending_sync["first"], False
        return {"sync_requested": requested, "restricted_mode": False}

    @app.get("/api/projects")
    def projects(request: Request) -> list[Any]:
        if not _authorized(request, token):
            raise HTTPException(status_code=401, detail="connect again")
        return []

    @app.post("/api/ingest")
    async def ingest(request: Request) -> dict[str, Any]:
        if not _authorized(request, token):
            raise HTTPException(status_code=401, detail="connect again")
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="not JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="not a chat")
        platform = str(payload.get("platform") or "").lower()
        key = _clean_id(payload.get("conversation_id"))
        markdown = payload.get("markdown")
        if platform not in CHAT_PLATFORMS or not key or not isinstance(markdown, str):
            raise HTTPException(status_code=400, detail="not a chat")
        if len(markdown) > MAX_MARKDOWN_CHARS:
            raise HTTPException(status_code=413, detail="chat too long")
        title = " ".join(str(payload.get("title") or "Chat").split())[:200]
        created = str(payload.get("created_at") or "")
        day = created[:10]
        if not _DAY.fullmatch(day):
            day = datetime.now(UTC).strftime("%Y-%m-%d")
        try:
            status, page = await asyncio.to_thread(
                saver.save, platform=platform, key=key, title=title, markdown=markdown, day=day
            )
        except (OSError, ImportRefused) as exc:
            log.warning("kept bridge could not save a %s chat: %s", platform, exc)
            raise HTTPException(status_code=500, detail="could not save") from exc
        skipped = status in ("unchanged", "refused")
        return {"status": status, "file_path": page.name, "skipped": skipped}

    return app


def port_free(port: int = KEPT_PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


async def serve_kept_bridge(vault_root: Path, db_path: Path) -> None:
    """Answer the Kept extension until cancelled; returns at once if the port is taken."""
    import uvicorn

    if not port_free():
        log.info("kept bridge: port %d is taken (the Kept app?); not started", KEPT_PORT)
        return
    saver = ChatSaver(vault_root, db_path)
    token = await asyncio.to_thread(load_token, db_path.parent)
    config = uvicorn.Config(
        build_app(saver, token), host="127.0.0.1", port=KEPT_PORT, log_level="warning",
        access_log=False, lifespan="off",
    )
    server = uvicorn.Server(config)
    # Ctrl+C belongs to the main Jarvis server; this one stops with its task.
    server.capture_signals = contextlib.nullcontext  # type: ignore[method-assign]
    try:
        await server.serve()
    finally:
        with contextlib.suppress(Exception):  # closing the index never blocks shutdown
            saver.close()
