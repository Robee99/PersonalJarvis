"""The live pages of the wiki vault, read once and shared.

The wiki routes (``/tree``, ``/graph``, ``/backlinks``, ``/orb-tree``) and the
memory tools all need the same view of the vault: every visible Markdown page,
parsed, with its vault-relative path. This module is that view and its parse
cache, so a page read for the orb is not read again for a tool call and the
two can never disagree about which pages exist or what they are called.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from jarvis.memory.orb_tree import TEXT_CHARS, NoteRow
from jarvis.memory.wiki.page import parse_markdown
from jarvis.memory.wiki.protocols import WikiPage
from jarvis.memory.wiki.wikilink import (
    _canonicalise as _canonicalise_link,  # type: ignore[attr-defined]
)

log = logging.getLogger(__name__)

# Canonical directories remain first in the tree for response compatibility.
PAGE_DIRS: tuple[tuple[str, str], ...] = (
    ("entities", "entity"),
    ("concepts", "concept"),
    ("projects", "project"),
    ("sessions", "session"),
)

# Frozen history and binary-only storage are not live Wiki pages. Hidden
# directories (for example .obsidian and .trash) are pruned separately.
EXCLUDED_PAGE_DIRS: frozenset[str] = frozenset(
    {"_archive", "attachments", "90-attachments"}
)
# Template scaffolding stays browsable in the tree but never enters the
# graph: its bodies are unrendered placeholders (``{{title}}``, ``[[…]]``),
# so every "link" would materialise as a phantom node.
TEMPLATE_DIRS: frozenset[str] = frozenset(
    {"templates", "_templates", "99-templates"}
)


def _title_of(page: WikiPage) -> str:
    """Best-effort human title: H1 in the body, else slug humanised."""
    for line in page.body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return page.slug.replace("-", " ").replace("_", " ").title()


def display_title(page: WikiPage) -> str:
    """Frontmatter title, else H1, else humanised slug — never a placeholder.

    Template pages carry unrendered Obsidian placeholders (``{{title}}``,
    ``{{date:dddd, MMMM Do YYYY}}``) as their H1; showing those verbatim in
    the tree or graph reads as junk, so any candidate containing ``{{``
    falls through to the next source.
    """
    for candidate in (str(page.frontmatter.get("title") or ""), _title_of(page)):
        candidate = candidate.strip()
        if candidate and "{{" not in candidate:
            return candidate
    return human_title_from_slug(page.slug)


def kind_of(page: WikiPage) -> str:
    """Schema kind (entity/concept/project/session/meta) of a page."""
    return page.page_type or "meta"


def human_title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


def link_slug(link: str) -> str:
    """Reduce a path-qualified Obsidian link to its page slug."""
    target = _canonicalise_link(link).replace("\\", "/").rsplit("/", 1)[-1]
    return target[:-3] if target.casefold().endswith(".md") else target


@dataclass(frozen=True, slots=True)
class VisiblePage:
    """One live Markdown page plus filesystem metadata."""

    page: WikiPage
    relative_path: Path
    mtime: float
    size: int


def _visible_path_sort_key(relative_path: Path) -> tuple[int, str]:
    """Keep canonical page directories first, then root and custom folders."""
    parts = relative_path.parts
    top = parts[0] if len(parts) > 1 else ""
    standard_order = {name: index for index, (name, _) in enumerate(PAGE_DIRS)}
    if top in standard_order:
        rank = standard_order[top]
    elif not top:
        rank = len(PAGE_DIRS)
    else:
        rank = len(PAGE_DIRS) + 1
    return rank, relative_path.as_posix().casefold()


# Parsed-page cache, keyed by vault root and then by vault-relative path.
#
# ``/tree``, ``/graph`` and ``/backlinks/{slug}`` all project the same vault
# walk, and each one used to READ AND PARSE every Markdown file in the vault
# on every single request. Opening the Wiki tab fires all three at once and
# every page click fires two more, so a vault with a few hundred pages spent
# whole seconds of disk + parse work per navigation — the user-visible "the
# wiki feels laggy" symptom.
#
# An entry is reused only when the file's ``(mtime_ns, size)`` is unchanged,
# which the walk has to stat for the response payload anyway. So a warm scan
# of an untouched vault costs one stat per file and zero reads/parses, while
# any external edit (Obsidian, the curator, a git pull) invalidates exactly
# the files it touched. Entries not seen in a scan are dropped, which bounds
# the cache by the vault itself; only the few most recent roots are kept.
_PARSE_CACHE_LOCK = threading.Lock()
_PARSE_CACHE: dict[str, dict[str, tuple[int, int, VisiblePage]]] = {}
_PARSE_CACHE_MAX_ROOTS = 4


def invalidate_visible_page_cache(vault_root: Path | None = None) -> None:
    """Drop cached page parses for one vault root, or for all of them.

    The ``(mtime_ns, size)`` check already covers every real edit; this exists
    for tests and for callers that rewrite a vault in place fast enough to
    land inside a filesystem's timestamp granularity.
    """
    with _PARSE_CACHE_LOCK:
        if vault_root is None:
            _PARSE_CACHE.clear()
        else:
            _PARSE_CACHE.pop(str(vault_root.resolve()), None)


def _walk_page_files(root: Path) -> list[tuple[Path, Path, os.stat_result]]:
    """Yield ``(path, vault-relative path, stat)`` for every visible page.

    Built on ``os.scandir`` rather than ``os.walk``: the directory entry
    already carries the file type and, on Windows, the full stat block, so the
    type check and the ``(mtime, size)`` the cache validates against come for
    free instead of costing a syscall each.

    Hidden trees, frozen archive history, and attachment stores are pruned
    before files are opened. Symlinked files that resolve outside the vault are
    ignored so a crafted vault cannot expose unrelated host files — and only a
    symlink can point outside, so the expensive ``resolve()`` is paid for links
    alone instead of for every page.
    """
    out: list[tuple[Path, Path, os.stat_result]] = []
    stack: list[str] = [str(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                listing = list(entries)
        except OSError as exc:
            log.warning("wiki_route_walk_failed: %s - %s", current, exc)
            continue
        for entry in listing:
            name = entry.name
            if name.startswith("."):
                continue
            try:
                # A symlinked directory reports False here, which is exactly
                # the old ``os.walk(followlinks=False)`` behaviour: listed,
                # never descended into.
                if entry.is_dir(follow_symlinks=False):
                    if name.casefold() not in EXCLUDED_PAGE_DIRS:
                        stack.append(entry.path)
                    continue
                if not name.casefold().endswith(".md"):
                    continue
                path = Path(entry.path)
                if entry.is_symlink():
                    resolved = path.resolve()
                    resolved.relative_to(root)
                    if not resolved.is_file():
                        continue
                elif not entry.is_file(follow_symlinks=False):
                    continue
                stat = entry.stat()
            except (OSError, ValueError):  # unreadable, or a symlink leaving the vault: not a page
                continue
            out.append((path, path.relative_to(root), stat))
    return out


def scan_visible_pages(vault_root: Path) -> list[VisiblePage]:
    """Parse every user-visible Markdown page below ``vault_root``.

    Unchanged files are served from the parse cache above; the walk itself is
    described in :func:`_walk_page_files`.
    """
    root = vault_root.resolve()
    cache_key = str(root)
    found = _walk_page_files(root)
    # One total-order sort at the end — the intermediate per-directory sorts
    # the old walk did could not change this result.
    found.sort(key=lambda item: _visible_path_sort_key(item[1]))

    with _PARSE_CACHE_LOCK:
        cached = _PARSE_CACHE.get(cache_key, {})

    fresh: dict[str, tuple[int, int, VisiblePage]] = {}
    visible: list[VisiblePage] = []
    for path, relative, stat in found:
        rel_key = relative.as_posix()
        signature = (stat.st_mtime_ns, stat.st_size)
        hit = cached.get(rel_key)
        if hit is not None and (hit[0], hit[1]) == signature:
            entry = hit[2]
        else:
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                log.warning("wiki_route_walk_failed: %s - %s", path, exc)
                continue
            entry = VisiblePage(
                page=parse_markdown(raw, path),
                relative_path=relative,
                mtime=stat.st_mtime,
                size=stat.st_size,
            )
        fresh[rel_key] = (*signature, entry)
        visible.append(entry)

    with _PARSE_CACHE_LOCK:
        # Re-insert so this root counts as the most recently used one.
        _PARSE_CACHE.pop(cache_key, None)
        _PARSE_CACHE[cache_key] = fresh
        while len(_PARSE_CACHE) > _PARSE_CACHE_MAX_ROOTS:
            del _PARSE_CACHE[next(iter(_PARSE_CACHE))]

    return visible


def note_rows(visible: Iterable[VisiblePage]) -> list[NoteRow]:
    """The memory orb's note rows: one per slug, templates left out.

    The first page with a slug wins, as in the graph. ``text`` carries the
    first :data:`~jarvis.memory.orb_tree.TEXT_CHARS` characters of the body,
    which is all the orb's topic finder reads.
    """
    rows: list[NoteRow] = []
    seen: set[str] = set()
    for item in visible:
        parts = item.relative_path.parts
        if len(parts) > 1 and parts[0].casefold() in TEMPLATE_DIRS:
            continue
        page = item.page
        if page.slug in seen:
            continue
        seen.add(page.slug)
        rows.append(
            NoteRow(
                slug=page.slug,
                title=display_title(page),
                kind=kind_of(page),
                folder=tuple(parts[:-1]),
                links=tuple(s for s in (link_slug(w) for w in page.wikilinks) if s),
                text=page.body[:TEXT_CHARS],
            )
        )
    return rows
