"""Bring a folder of notes (an Obsidian vault, ``C:\\AI DATA``) into the wiki.

The wiki is a folder of Markdown pages plus a derived FTS index. An import is
an adapter onto exactly that: every supported file in the chosen source lands
as a Markdown page under ``imports/<source>/`` in the canonical vault, keeping
its relative path, and the pages written are upserted into the FTS index in
batches. Nothing else is created: no second store, no import database.

* **Idempotent.** The page on disk is the import state. The same source with
  the same content changes nothing; a changed file updates its page; an
  interrupted run is finished by running it again.
* **Bounded.** Files are walked lazily and read one at a time, under a size
  cap, so a large source never sits in memory.
* **Safe.** Symlinks are not followed, hidden folders (``.obsidian``,
  ``.git``, ``.trash``) and the index's own exclusions are skipped, the vault
  is never imported into itself, every destination is checked to stay inside
  the import folder, and a file that looks like it holds a key or token is
  skipped rather than copied (``secret_guard``). Content is only ever read as
  text and written as text.

Markdown is copied byte-for-byte so frontmatter, tags and ``[[wikilinks]]``
keep working; plain text becomes a page titled after its file name. Documents
(PDF, Office, HTML, CSV, JSON, code…) become one page of their text, and AI
chat exports (ChatGPT, Claude, chat datasets) one page per conversation
(``import_formats``). In converted pages a key or token is masked
(``redact_secrets``) and the page is skipped if one is still found. Files with
no text (pictures, archives, old Office files) are counted with the reason.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from jarvis.core.redact import redact_secrets
from jarvis.memory.wiki import fts_index
from jarvis.memory.wiki.import_formats import (
    NO_TEXT_REASONS,
    NoText,
    Page,
    conversation_pages,
    document_page,
)
from jarvis.memory.wiki.secret_guard import contains_secret

log = logging.getLogger(__name__)

#: Folder in the vault that holds every import, one subfolder per source.
IMPORT_DIR: Final[str] = "imports"

#: Extensions read as Markdown (copied as-is) or plain text (wrapped).
MARKDOWN_SUFFIXES: Final[frozenset[str]] = frozenset({".md", ".markdown"})
TEXT_SUFFIXES: Final[frozenset[str]] = frozenset({".txt"})

#: A note bigger than this is reported, not imported.
MAX_FILE_BYTES: Final[int] = 2_000_000
#: The same for a document (PDF, Office…) and for a chat export, which are
#: mostly markup and metadata around the text.
MAX_DOCUMENT_BYTES: Final[int] = 64 * 1024 * 1024
MAX_EXPORT_BYTES: Final[int] = 256 * 1024 * 1024
#: JSON that may be an AI chat export or dataset.
EXPORT_SUFFIXES: Final[frozenset[str]] = frozenset({".json", ".jsonl", ".ndjson"})

#: Pages per FTS commit.
INDEX_BATCH: Final[int] = 50

#: Folders never walked, besides hidden ones and the index's exclusions.
_SKIP_DIRS: Final[frozenset[str]] = frozenset(
    {"node_modules", "__pycache__", *fts_index.SKIP_INDEX_DIRS}
)

#: Characters Windows refuses in a file name.
_UNSAFE_NAME = re.compile(r'[<>:"|?*\x00-\x1f]')


class ImportRefused(ValueError):
    """The chosen source cannot be imported at all (missing, inside the vault…)."""


@dataclass(slots=True)
class ImportProgress:
    """What an import has done so far. Paths are relative to the source."""

    source: str
    destination: str = ""
    phase: str = "queued"
    obsidian_vault: bool = False
    discovered: int = 0
    supported: int = 0
    processed: int = 0
    imported: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    indexed: int = 0
    total_bytes: int = 0
    current: str = ""
    extensions: Counter[str] = field(default_factory=Counter)
    skip_reasons: Counter[str] = field(default_factory=Counter)
    skipped_types: Counter[str] = field(default_factory=Counter)
    conversations: int = 0
    problems: list[dict[str, str]] = field(default_factory=list)
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "destination": self.destination,
            "phase": self.phase,
            "obsidian_vault": self.obsidian_vault,
            "discovered": self.discovered,
            "supported": self.supported,
            "processed": self.processed,
            "imported": self.imported,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "failed": self.failed,
            "indexed": self.indexed,
            "total_bytes": self.total_bytes,
            "current": self.current,
            "extensions": dict(self.extensions.most_common()),
            "skip_reasons": dict(self.skip_reasons),
            "skipped_types": dict(self.skipped_types.most_common(12)),
            "conversations": self.conversations,
            "problems": list(self.problems[:50]),
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def source_label(source: Path) -> str:
    """The folder name an import of ``source`` gets under ``imports/``.

    The readable name plus a short hash of the absolute path, so two sources
    with the same folder name never share pages and a re-import of the same
    source always lands in the same place.
    """
    name = re.sub(r"[^a-z0-9]+", "-", source.name.lower()).strip("-") or "source"
    digest = hashlib.sha256(str(source).lower().encode("utf-8")).hexdigest()[:6]
    return f"{name[:40]}-{digest}"


def check_source(source: Path, vault_root: Path) -> Path:
    """Resolve ``source`` and refuse what can never be imported."""
    try:
        resolved = source.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ImportRefused(f"{source} does not exist or cannot be opened.") from exc
    vault = vault_root.resolve()
    if resolved == vault or vault in resolved.parents:
        raise ImportRefused("That folder is already part of Jarvis memory.")
    if not (resolved.is_dir() or resolved.is_file()):
        raise ImportRefused(f"{source} is not a folder or a file.")
    return resolved


def is_obsidian_vault(source: Path) -> bool:
    """An Obsidian vault keeps its settings in a ``.obsidian`` folder."""
    return source.is_dir() and (source / ".obsidian").is_dir()


def iter_source_files(source: Path, vault_root: Path) -> Iterator[Path]:
    """Every regular file under ``source``, sorted, without following links.

    Hidden folders, the index's excluded folders and the vault itself (when it
    sits inside the source) are skipped.
    """
    if source.is_file():
        yield source
        return
    vault = vault_root.resolve()
    for current, dirnames, filenames in os.walk(source, followlinks=False):
        here = Path(current)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".")
            and name not in _SKIP_DIRS
            and not (here / name).is_symlink()
            and (here / name).resolve() != vault
        )
        for name in sorted(filenames):
            path = here / name
            if name.startswith(".") or path.is_symlink() or not path.is_file():
                continue
            yield path


def destination_for(relative: Path, import_root: Path) -> Path:
    """The vault page for a source file at ``relative``, kept inside ``import_root``."""
    parts = [
        _UNSAFE_NAME.sub("_", part).strip(" .") or "_"
        for part in relative.parts
        if part not in ("", ".", "..")
    ]
    if not parts:
        raise ImportRefused("empty relative path")
    leaf = Path(parts[-1])
    if leaf.suffix.lower() != ".md":
        parts[-1] = f"{leaf.stem or leaf.name}.md"
    target = import_root.joinpath(*parts)
    root = import_root.resolve()
    if root not in target.resolve().parents:
        raise ImportRefused(f"{relative} would land outside the import folder")
    return target


def page_text(path: Path, raw: bytes) -> str:
    """The page body for one source file; raises ``ValueError`` if it is not text."""
    if b"\x00" in raw[:8192]:
        raise ValueError("binary content")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:  # not UTF-8: Windows notes are often cp1252
        try:
            text = raw.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise ValueError("not readable as text") from exc
    if not text.strip():
        raise ValueError("empty")
    if path.suffix.lower() in MARKDOWN_SUFFIXES:
        return text
    return f"# {path.stem}\n\n{text.rstrip()}\n"


def write_page_atomic(target: Path, content: str) -> None:
    """Tempfile, fsync and replace, as the wiki's own writer does."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".tmp", prefix=f"{target.name}.", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()


class _Indexer:
    """Upserts written pages into the FTS index, one transaction per batch."""

    def __init__(self, vault_root: Path, db_path: Path | None) -> None:
        self._vault_root = vault_root
        self._db_path = db_path
        self._pending: list[Path] = []
        self._conn: sqlite3.Connection | None = None
        self.indexed = 0

    def add(self, path: Path) -> None:
        self._pending.append(path)
        if len(self._pending) >= INDEX_BATCH:
            self.flush()

    def flush(self) -> None:
        if not self._pending or self._db_path is None:
            self._pending.clear()
            return
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            fts_index.ensure_schema(self._conn)
        self.indexed += fts_index.upsert_pages(self._conn, self._vault_root, self._pending)
        self._pending.clear()

    def close(self) -> None:
        try:
            self.flush()
        finally:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


def run_import(
    source: Path,
    vault_root: Path,
    *,
    db_path: Path | None,
    progress: ImportProgress | None = None,
    cancel: threading.Event | None = None,
) -> ImportProgress:
    """Import ``source`` into ``vault_root`` and index what changed. Blocking."""
    progress = progress or ImportProgress(source=str(source))
    progress.started_at = progress.started_at or time.time()
    stop = cancel or threading.Event()
    resolved = check_source(source, vault_root)
    progress.source = str(resolved)
    progress.obsidian_vault = is_obsidian_vault(resolved)
    import_root = vault_root / IMPORT_DIR / source_label(resolved)
    progress.destination = import_root.relative_to(vault_root).as_posix()
    base = resolved if resolved.is_dir() else resolved.parent

    indexer = _Indexer(vault_root, db_path)
    progress.phase = "importing"
    try:
        for path in iter_source_files(resolved, vault_root):
            if stop.is_set():
                progress.phase = "cancelled"
                break
            relative = path.relative_to(base)
            progress.discovered += 1
            progress.current = relative.as_posix()
            suffix = path.suffix.lower() or "(none)"
            progress.extensions[suffix] += 1
            _import_one(path, relative, import_root, progress, indexer)
            progress.processed += 1
        progress.phase = "indexing" if progress.phase != "cancelled" else "cancelled"
        indexer.close()
        progress.indexed = indexer.indexed
        if progress.phase == "indexing":
            progress.phase = "done"
    except Exception as exc:  # noqa: BLE001 — the job reports, the pages written stay valid
        log.exception("wiki import of %s failed", progress.source)
        progress.phase = "failed"
        progress.error = f"{type(exc).__name__}: {exc}"
        indexer.close()
        progress.indexed = indexer.indexed
    progress.current = ""
    progress.finished_at = time.time()
    return progress


def _import_one(
    path: Path,
    relative: Path,
    import_root: Path,
    progress: ImportProgress,
    indexer: _Indexer,
) -> None:
    suffix = path.suffix.lower()
    verbatim = suffix in MARKDOWN_SUFFIXES | TEXT_SUFFIXES
    limit = (
        MAX_FILE_BYTES if verbatim
        else MAX_EXPORT_BYTES if suffix in EXPORT_SUFFIXES
        else MAX_DOCUMENT_BYTES
    )
    try:
        size = path.stat().st_size
        progress.total_bytes += size
        if size > limit:
            _skip(progress, relative, "too large")
            return
        raw = path.read_bytes() if verbatim or suffix in EXPORT_SUFFIXES else b""
    except OSError as exc:  # reported per file in the job's problems list
        _fail(progress, relative, f"unreadable ({type(exc).__name__})")
        return
    if verbatim:
        try:
            content = page_text(path, raw)
        except ValueError as exc:  # binary or empty: reported as a skip reason
            _skip(progress, relative, str(exc))
            return
        progress.supported += 1
        if contains_secret(content):
            _skip(progress, relative, "looks like it holds a key or token")
            return
        _write(destination_for(relative, import_root), content, relative, progress, indexer)
        return

    pages: list[Page] | None = None
    if suffix in EXPORT_SUFFIXES:
        pages = conversation_pages(path, raw)
        if pages is not None:
            progress.conversations += len(pages)
    if pages is None:
        try:
            pages = [document_page(path)]
        except NoText as exc:  # pictures, archives…: counted with the reason
            _skip(progress, relative, str(exc))
            return
        target_of = {pages[0].name: relative.parent / f"{relative.name}.md"}
    else:
        folder = relative.parent / relative.stem
        target_of = {page.name: folder / f"{page.name}.md" for page in pages}
    progress.supported += 1
    for page in pages:
        body = redact_secrets(page.body)
        if contains_secret(body):
            _skip(progress, relative, "looks like it holds a key or token")
            continue
        try:
            target = destination_for(target_of[page.name], import_root)
        except ImportRefused:  # reported per page in the job's problems list
            _fail(progress, relative, "could not name its page")
            continue
        _write(target, body, relative, progress, indexer)


def _write(
    target: Path,
    content: str,
    relative: Path,
    progress: ImportProgress,
    indexer: _Indexer,
) -> None:
    try:
        existing = (
            target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
        )
        # Decoders may preserve CRLF while read_text uses universal newlines.
        # Canonical Markdown newlines make a Windows reimport idempotent.
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        if existing == content:
            progress.unchanged += 1
            return
        write_page_atomic(target, content)
    except OSError as exc:  # reported per file in the job's problems list
        _fail(progress, relative, f"could not write ({type(exc).__name__})")
        return
    if existing is None:
        progress.imported += 1
    else:
        progress.updated += 1
    indexer.add(target)


#: Skip reasons counted but not listed file by file: they are the normal
#: contents of a folder, not problems.
_QUIET_REASONS: Final[frozenset[str]] = frozenset({"empty", *NO_TEXT_REASONS.values()})


def _skip(progress: ImportProgress, relative: Path, reason: str) -> None:
    progress.skipped += 1
    progress.skip_reasons[reason] += 1
    progress.skipped_types[f"{relative.suffix.lower() or '(no extension)'}: {reason}"] += 1
    if reason not in _QUIET_REASONS:
        progress.problems.append({"path": relative.as_posix(), "reason": reason})


def _fail(progress: ImportProgress, relative: Path, reason: str) -> None:
    progress.failed += 1
    progress.problems.append({"path": relative.as_posix(), "reason": reason})


# ---------------------------------------------------------------- jobs


@dataclass(slots=True)
class ImportJob:
    job_id: str
    progress: ImportProgress
    cancel: threading.Event
    thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


class ImportJobs:
    """One import at a time, run on a worker thread so the app stays responsive."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, ImportJob] = {}

    def start(
        self,
        source: Path,
        vault_root: Path,
        *,
        db_path: Path | None,
        on_done: Callable[[ImportProgress], None] | None = None,
    ) -> ImportJob:
        resolved = check_source(source, vault_root)
        with self._lock:
            if any(job.running for job in self._jobs.values()):
                raise RuntimeError("An import is already running.")
            job = ImportJob(
                uuid.uuid4().hex[:12], ImportProgress(source=str(resolved)), threading.Event()
            )
            job.progress.started_at = time.time()

            def work() -> None:
                run_import(
                    resolved, vault_root, db_path=db_path, progress=job.progress, cancel=job.cancel
                )
                if on_done is not None:
                    on_done(job.progress)

            job.thread = threading.Thread(
                target=work, name=f"wiki-import-{job.job_id}", daemon=True
            )
            self._jobs[job.job_id] = job
            job.thread.start()
            return job

    def get(self, job_id: str) -> ImportJob | None:
        return self._jobs.get(job_id)

    def latest(self) -> ImportJob | None:
        return next(reversed(self._jobs.values()), None) if self._jobs else None

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        job.cancel.set()
        return True


#: The app's one import queue.
IMPORT_JOBS: Final[ImportJobs] = ImportJobs()


__all__ = [
    "IMPORT_DIR",
    "IMPORT_JOBS",
    "ImportJob",
    "ImportJobs",
    "ImportProgress",
    "ImportRefused",
    "check_source",
    "destination_for",
    "is_obsidian_vault",
    "iter_source_files",
    "run_import",
    "source_label",
]
