"""The H.O.L.O. hand-gesture deck, served from Jarvis with its orbs filled from
the wiki vault.

``holo/holo.html`` is Zubair Trabzada's holo-gestures page (MIT, see
``holo/LICENSE``), kept byte for byte. Upstream it runs beside a small
stand-alone ``server.py``; here the same page is served by the Jarvis web
server instead, so there is no second daemon, and its notes come from the
user's own memory: every top-level vault folder becomes one floating orb and
its newest pages become the cards inside it.

The page and its static files live under ``/holo``; everything that reads the
vault or changes the machine lives under ``/api/holo``, so it sits behind the
same session check as the rest of the API (SurfaceSecurity leaves non-``/api``
GETs open, which is right for a page and wrong for notes):

    GET  /holo/                the page, with its absolute ``/api``, ``/vendor``
                               and ``/props`` paths moved to the routes below
    GET  /holo/vendor/...      the downloaded tracking files
    GET  /api/holo/tree        vault folders as orbs: [{kind, name, files: [...]}]
    GET  /api/holo/props       [] - the 3D scans are not shipped
    POST /api/holo/state       acknowledged; nothing consumes gesture state yet
    POST /api/holo/diag        the page's own crash report, logged at debug
    GET  /api/holo/install     whether the tracking files are on this machine
    POST /api/holo/install     download them once (about 32 MB)

The hand-tracking files (MediaPipe Tasks Vision, Apache-2.0; three.js, MIT)
are not in the repository. Upstream patches MediaPipe's wasm glue with a
one-line shim, so the CDN copy the page falls back to is not equivalent.
Instead, the first open downloads the exact upstream files from GitHub,
pinned to one commit and checked against SHA-256 digests, into the user data
directory. Camera frames never leave the browser.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from jarvis.core.paths import user_data_dir
from jarvis.ui.web.wiki_routes import _resolve_vault_root

log = logging.getLogger(__name__)

router = APIRouter(prefix="/holo", tags=["holo"])
api_router = APIRouter(prefix="/api/holo", tags=["holo"])

PAGE = Path(__file__).with_name("holo") / "holo.html"

#: Same shape limits as upstream's server: a deck, not a file browser.
MAX_ORBS = 12
MAX_FILES_PER_ORB = 14
_TITLE_CHARS = 48
_BODY_CHARS = 420
_FULL_CHARS = 4000
_SKIPPED_DIRS = frozenset(
    {"_archive", "attachments", "90-attachments", "templates", "_templates", "99-templates"}
)
_FRONTMATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
_PATH_PREFIXES = (
    ("'/api/", "'/api/holo/"),
    ("/vendor/", "/holo/vendor/"),
    ("'/props/", "'/holo/props/"),
)


#: holo-gestures commit the page and the tracking files come from.
HOLO_COMMIT = "55626ff00f6b49c649a407ffdb3cad174479c637"
_RAW_URL = "https://raw.githubusercontent.com/zubair-trabzada/holo-gestures/{commit}/vendor/{path}"
#: Every file the page may load from ``/vendor``, with its SHA-256 at HOLO_COMMIT.
VENDOR_FILES: dict[str, str] = {
    "BufferGeometryUtils.js": "9be041e96308775d00e2695cc607645b9a9b64fd7c0e759dd8f7c00a8d92becb",
    "GLTFLoader.js": "3d8d4b6b2c7e9b0690fe4b464d4fbb0faca5eda0b1d86391405b9634c1a11355",
    "hand_landmarker.task": "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1",
    "three.module.js": "76dea8151bc9352aef3528b4262e249b2604f62543828328db978d060d61a495",
    "vision_bundle.mjs": "d885630c297c0b20b1fe86096cb06291c4c8080876f27852e724f24ac603713f",
    "wasm/vision_wasm_internal.js": (
        "1fbb0755ecb357a14b6bb40e5517dd77a1f46be3cf38aaf37386ac25a64fd50c"
    ),
    "wasm/vision_wasm_internal.wasm": (
        "8da277a733926eacd0474b8704b36742d6ec3231c57a860c5b889dff8f1df886"
    ),
    "wasm/vision_wasm_nosimd_internal.js": (
        "19b6b0211788b48802911a53f45bb701efa8507d5b9c5f7bbb2da59a0b935f71"
    ),
    "wasm/vision_wasm_nosimd_internal.wasm": (
        "a28483cd42e74e855bf5ebdb6b40d9b66a5b49e35e95020bc97669e6822a3192"
    ),
}
_MIME = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".wasm": "application/wasm",
    ".task": "application/octet-stream",
}
_install_lock = asyncio.Lock()


def vendor_dir() -> Path:
    """Where the downloaded tracking files live (outside the install folder)."""
    return user_data_dir() / "holo" / "vendor"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def missing_vendor_files(root: Path) -> list[str]:
    """Files that are absent; a present file is trusted (it was verified on write)."""
    return [name for name in VENDOR_FILES if not (root / name).is_file()]


async def install_vendor_files(root: Path, client: httpx.AsyncClient) -> None:
    """Download every missing file, verify its digest, then move it into place."""
    for name in missing_vendor_files(root):
        url = _RAW_URL.format(commit=HOLO_COMMIT, path=name)
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with part.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    handle.write(chunk)
        actual = await asyncio.to_thread(_sha256, part)
        if actual != VENDOR_FILES[name]:
            part.unlink(missing_ok=True)
            raise ValueError(f"{name} did not match its pinned digest")
        part.replace(target)


def rewrite_page(html: str) -> str:
    """Move the page's absolute paths under ``/holo`` so they cannot collide
    with the app's own ``/api``."""
    for old, new in _PATH_PREFIXES:
        html = html.replace(old, new)
    return html


def _card(path: Path) -> dict[str, str] | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        log.debug("holo: could not read %s", path, exc_info=True)
        return None
    text = _FRONTMATTER.sub("", text, count=1)
    lines = [line for line in text.splitlines() if line.strip()]
    title = (lines[0].lstrip("# ").strip() if lines else "")[:_TITLE_CHARS] or path.stem
    rest = [line for line in lines[1:] if not line.startswith("#")]
    return {
        "name": path.name,
        "title": title,
        "body": "\n".join(rest)[:_BODY_CHARS],
        "full": "\n".join(lines[1:])[:_FULL_CHARS],
    }


def _newest_first(paths: list[Path]) -> list[Path]:
    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:  # a page deleted mid-scan sorts last
            return 0.0

    return sorted(paths, key=mtime, reverse=True)


def vault_orbs(root: Path | None) -> list[dict[str, Any]]:
    """Top-level vault folders as orbs; loose root pages gather under NOTES."""
    if root is None or not root.is_dir():
        return []
    orbs: list[dict[str, Any]] = []
    loose: list[Path] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith(".") or entry.name.lower() in _SKIPPED_DIRS:
            continue
        if entry.is_dir():
            pages = _newest_first([p for p in entry.rglob("*.md") if p.is_file()])
            cards = [c for p in pages[:MAX_FILES_PER_ORB] if (c := _card(p))]
            if cards:
                orbs.append({"kind": "folder", "name": entry.name.upper()[:22], "files": cards})
        elif entry.suffix == ".md":
            loose.append(entry)
    if loose:
        cards = [c for p in _newest_first(loose)[:MAX_FILES_PER_ORB] if (c := _card(p))]
        orbs.append({"kind": "folder", "name": "NOTES", "files": cards})
    return orbs[:MAX_ORBS]


@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, summary="The HOLO hand-gesture deck")
def page() -> HTMLResponse:
    try:
        html = PAGE.read_text(encoding="utf-8")
    except OSError:
        log.warning("holo: page missing at %s", PAGE, exc_info=True)
        return HTMLResponse("HOLO is not installed in this build.", status_code=404)
    return HTMLResponse(rewrite_page(html), headers={"Cache-Control": "no-store"})


@router.get("/vendor/{path:path}", include_in_schema=False)
def vendor(path: str) -> Response:
    # Only the pinned names are ever served, so no request path reaches the
    # filesystem. Anything else is a plain 404 rather than the SPA fallback.
    if path not in VENDOR_FILES:
        return Response(status_code=404)
    target = vendor_dir() / path
    if not target.is_file():
        return Response(status_code=404)
    return FileResponse(target, media_type=_MIME.get(target.suffix, "application/octet-stream"))


@router.get("/props/{path:path}", include_in_schema=False)
def no_props(path: str) -> Response:
    return Response(status_code=404)


@api_router.get("/install", summary="Whether the hand-tracking files are installed")
def install_status() -> dict[str, Any]:
    missing = missing_vendor_files(vendor_dir())
    return {"ready": not missing, "missing": missing}


@api_router.post("/install", summary="Download the pinned hand-tracking files once")
async def install() -> JSONResponse:
    async with _install_lock:
        root = vendor_dir()
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
                await install_vendor_files(root, client)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            log.warning("holo: installing the tracking files failed", exc_info=True)
            return JSONResponse(
                {"ready": False, "error": f"Downloading HOLO failed: {exc}"}, status_code=502
            )
    return JSONResponse({"ready": True})


@api_router.get("/tree", summary="Wiki vault folders as HOLO orbs")
def tree(request: Request) -> list[dict[str, Any]]:
    return vault_orbs(_resolve_vault_root(request))


@api_router.get("/props", summary="Grabbable 3D props (none shipped)")
def props() -> list[str]:
    return []


@api_router.post("/state", summary="Gesture state from the page")
def state() -> dict[str, bool]:
    # Upstream writes this to a file "so the big brain can react"; nothing in
    # Jarvis consumes it yet, so it is acknowledged and dropped.
    return {"ok": True}


@api_router.post("/diag", summary="The page's own crash report")
async def diag(request: Request) -> dict[str, bool]:
    try:
        body = (await request.body())[:4000]
    except Exception:  # noqa: BLE001 - a broken diagnostic must not error the page
        body = b""
    log.debug("holo: page diagnostics %s", body.decode("utf-8", errors="replace"))
    return {"ok": True}
