"""Delete every note in the wiki vault, after one full snapshot.

Roberto 2026-10-26: "there's no way to delete old notes data ... delete the
current notes, all of it", before importing his ChatGPT and Claude exports
fresh. The vault is a folder of Markdown pages plus a derived FTS index, so
deleting the notes means removing the pages and emptying the index; nothing
else stores them.

Kept: hidden entries (``.obsidian``), the template folder and the scaffold
pages the vault is created with (``README.md``, ``index.md``, ``schema.md``).
Before anything is removed the whole vault, ``_archive/`` and attachments
included, is written to one ``wiki-wipe-<time>.tar.gz`` next to the other wiki
backups, so the deletion can be undone by unpacking it.
"""

from __future__ import annotations

import datetime as _dt
import logging
import shutil
import sqlite3
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from jarvis.memory.wiki import fts_index

log = logging.getLogger(__name__)

#: What the person types to confirm; the route refuses anything else.
CONFIRM_PHRASE: Final[str] = "DELETE ALL NOTES"

#: Vault entries a wipe leaves in place.
_KEEP_NAMES: Final[frozenset[str]] = frozenset(
    {"README.md", "index.md", "schema.md", "99-templates"}
)


@dataclass(frozen=True, slots=True)
class WipeResult:
    """What a wipe removed and where the snapshot of it is."""

    pages: int
    entries: int
    backup: str

    def to_dict(self) -> dict[str, int | str]:
        return {"pages": self.pages, "entries": self.entries, "backup": self.backup}


def _doomed(vault_root: Path) -> list[Path]:
    return sorted(
        entry
        for entry in vault_root.iterdir()
        if not entry.name.startswith(".") and entry.name not in _KEEP_NAMES
    )


def _snapshot(vault_root: Path, backup_dir: Path, now: _dt.datetime) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"wiki-wipe-{now.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    with tarfile.open(target, "w:gz") as tar:
        tar.add(vault_root, arcname=".", recursive=True)
    return target


def wipe_notes(
    vault_root: Path,
    *,
    db_path: Path | None,
    backup_dir: Path,
    now: _dt.datetime | None = None,
) -> WipeResult:
    """Remove every note page under ``vault_root`` and empty its search index."""
    vault = vault_root.resolve()
    if not vault.is_dir():
        raise FileNotFoundError(f"{vault} is not a folder")
    backup = backup_dir.resolve()
    if backup == vault or vault in backup.parents:
        raise ValueError("the backup folder must be outside the vault")
    doomed = _doomed(vault)
    pages = sum(
        1
        for entry in doomed
        for path in ([entry] if entry.is_file() else entry.rglob("*.md"))
        if path.suffix.lower() == ".md"
    )
    archive = _snapshot(vault, backup, now or _dt.datetime.now())
    for entry in doomed:
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    if db_path is not None and db_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            fts_index.ensure_schema(conn)
            conn.execute("DELETE FROM wiki_fts")
            conn.commit()
        finally:
            conn.close()
    log.warning(
        "wiki wipe: removed %d entries (%d pages) from %s; snapshot %s",
        len(doomed), pages, vault, archive,
    )
    return WipeResult(pages=pages, entries=len(doomed), backup=str(archive))


__all__ = ["CONFIRM_PHRASE", "WipeResult", "wipe_notes"]
