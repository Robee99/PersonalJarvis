"""Bring chats saved from the browser into memory on their own.

AI Chat Exporter (github.com/nikolaylosev/ai-chat-exporter, MIT; bundled in
``jarvis/assets/ai-chat-exporter``) saves ChatGPT, Claude and Gemini chats
from the owner's signed-in Chrome as Markdown into ``Downloads/AI Chats``, and
the council extension saves its verdicts there too. This runs the ordinary
memory import on that folder whenever something in it changed, so a chat
saved in Chrome shows up in memory without opening Import Data. The importer
already makes the same chat one page (by its conversation id), so a chat saved
twice, or also in an export ZIP, is updated, not repeated.

The only state is the newest change time already imported, one number per
folder, in ``wiki_chat_sync.json`` next to the wiki index.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Final

from jarvis.memory.wiki.importer import IMPORT_JOBS, ImportRefused

log = logging.getLogger(__name__)

#: Seconds between looks at the chat folders.
SYNC_INTERVAL_S: Final[float] = 300.0
#: Seconds after start before the first look, so boot is not slowed.
FIRST_SYNC_DELAY_S: Final[float] = 90.0
STATE_FILE: Final[str] = "wiki_chat_sync.json"


def chat_folders(home: Path | None = None) -> list[Path]:
    """The folders browser-saved chats land in, existing or not."""
    base = home or Path.home()
    return [base / "Downloads" / "AI Chats", base / "Documents" / "AI Chats"]


def latest_change(folder: Path) -> float:
    """The newest modification time of anything in ``folder`` (0 when empty)."""
    newest = 0.0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            try:
                newest = max(newest, os.stat(os.path.join(root, name)).st_mtime)
            except OSError:  # a file removed while walking: nothing to import
                continue
    return newest


def _load(state_path: Path) -> dict[str, float]:
    try:
        data = json.loads(state_path.read_text("utf-8"))
    except (OSError, ValueError):  # first run or a damaged file: import everything once
        return {}
    return {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))}


def _save(state_path: Path, state: dict[str, float]) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=1), "utf-8")
    except OSError:  # the next look imports again; pages come out unchanged
        log.warning("chat sync could not save %s", state_path, exc_info=True)


def changed_folders(folders: list[Path], state: dict[str, float]) -> list[tuple[Path, float]]:
    """``(folder, newest change)`` for every existing folder with something new."""
    found = []
    for folder in folders:
        if not folder.is_dir():
            continue
        newest = latest_change(folder)
        if newest > state.get(str(folder), 0.0):
            found.append((folder, newest))
    return found


async def sync_once(
    vault_root: Path,
    db_path: Path,
    *,
    folders: list[Path] | None = None,
) -> list[str]:
    """Import every chat folder that changed; the folders imported."""
    state_path = db_path.parent / STATE_FILE
    state = _load(state_path)
    done: list[str] = []
    changed = await asyncio.to_thread(changed_folders, folders or chat_folders(), state)
    for folder, newest in changed:
        if IMPORT_JOBS.busy():
            break
        try:
            job = IMPORT_JOBS.start(folder, vault_root, db_path=db_path)
        except (ImportRefused, RuntimeError) as exc:
            log.info("chat sync skipped %s: %s", folder, exc)
            continue
        if job.thread is not None:
            await asyncio.to_thread(job.thread.join)
        if job.progress.phase == "done":
            state[str(folder)] = newest
            _save(state_path, state)
            done.append(str(folder))
            log.info(
                "chat sync %s: %d new, %d updated", folder, job.progress.imported,
                job.progress.updated,
            )
    return done


async def chat_sync_loop(target: Callable[[], tuple[Path, Path] | None]) -> None:
    """Forever: look at the chat folders and import what changed.

    ``target`` gives the vault root and index path, or ``None`` while memory
    is not set up.
    """
    await asyncio.sleep(FIRST_SYNC_DELAY_S)
    while True:
        try:
            where = target()
            if where is not None:
                await sync_once(*where)
        except Exception:  # noqa: BLE001 — one failed look never stops the next
            log.warning("chat sync failed", exc_info=True)
        await asyncio.sleep(SYNC_INTERVAL_S)
