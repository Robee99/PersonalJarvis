"""find_files tool: find files and folders by name anywhere on this computer.

Live 2026-10-26 Roberto asked Jarvis to search his C: drive and it could not:
the only file tools were the filesystem MCP server, which is limited to three
folders, and Desktop Commander's background search, which a small model does
not drive to the end. This tool answers in one call.

Two passes, both read-only:

* **The Windows Search index** (Windows only): one ``SystemIndex`` query
  through ADODB in a short PowerShell call. Instant for the folders Windows
  indexes (the user's own folders, by default).
* **A walk of the drive**, user folders first, then everything else under the
  root, skipping the system's own folders (``C:\\Windows``, the recycle bin,
  ``node_modules`` ...). It stops at the result limit or the time budget and
  says how far it got, so "not found" is never claimed for a drive it did not
  finish.

Names are matched, never file contents. Risk tier: safe; nothing is opened,
moved or changed.
"""

from __future__ import annotations

import asyncio
import collections
import datetime as _dt
import fnmatch
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)

_KINDS = ("any", "file", "folder")
_DEFAULT_LIMIT = 25
_MAX_LIMIT = 200
#: How long one call may walk the disk.
_WALK_BUDGET_S = 25.0
_INDEX_TIMEOUT_S = 8.0

#: Folders the walk never enters: the system's own, caches and package trees.
_SKIP_NAMES = frozenset({
    "$recycle.bin", "system volume information", "$windows.~bt", "$windows.~ws",
    "$winreagent", "config.msi", "recovery", "perflogs", "msocache", "winsxs",
    "node_modules", ".git", "__pycache__", ".venv", "site-packages",
})
#: Skipped only directly under a drive root: ``C:\\Windows`` but not a
#: project folder that happens to be called "windows".
_SKIP_AT_ROOT = frozenset({"windows"})
#: Searched before the rest of the drive, under the user's profile.
_USER_FOLDERS = ("Desktop", "Downloads", "Documents", "Pictures", "Videos", "Music", "OneDrive")


@dataclass
class _Found:
    paths: list[Path] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    folders: int = 0
    finished: bool = False
    from_index: int = 0

    def add(self, path: Path) -> None:
        key = os.path.normcase(str(path))
        if key not in self.seen:
            self.seen.add(key)
            self.paths.append(path)


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[\s_\-.]+", text.casefold()) if w]


def matcher(query: str, extensions: list[str]) -> Any:
    """A test on an entry's name: a glob when the query has ``*``/``?``, else all its words."""
    exts = {"." + e.casefold().lstrip(".") for e in extensions if e.strip(". ")}
    pattern = query.strip().casefold()
    glob = any(c in pattern for c in "*?")
    words = _words(pattern)

    def test(name: str) -> bool:
        low = name.casefold()
        if exts and os.path.splitext(low)[1] not in exts:
            return False
        if glob:
            return fnmatch.fnmatchcase(low, pattern)
        if not words:
            return bool(exts)
        flat = " ".join(_words(low))
        return all(w in flat for w in words)

    return test


def _kind_ok(is_dir: bool, kind: str) -> bool:
    return kind == "any" or (kind == "folder") == is_dir


def _roots(root: str) -> list[Path]:
    if root.strip():
        return [Path(os.path.expandvars(os.path.expanduser(root.strip())))]
    if os.name == "nt":
        system = os.environ.get("SystemDrive", "C:")
        return [Path(system + "\\")]
    return [Path.home()]


def _start_points(roots: list[Path], whole_drive: bool) -> list[Path]:
    """User folders first when a whole drive is searched, then the roots."""
    if not whole_drive:
        return roots
    home = Path.home()
    first = [home / name for name in _USER_FOLDERS if (home / name).is_dir()]
    return [*first, home, *roots]


def _index_query(query: str, extensions: list[str], roots: list[Path], limit: int) -> str:
    words = [re.sub(r"[^\w .-]", "", w) for w in _words(query.replace("*", " ").replace("?", " "))]
    clauses = [f"System.FileName LIKE '%{w}%'" for w in words if w]
    for ext in extensions:
        clean = re.sub(r"[^\w]", "", ext)
        if clean:
            clauses.append(f"System.FileExtension = '.{clean}'")
    if not clauses:
        return ""
    scopes = " OR ".join(
        "SCOPE='file:" + re.sub(r"['\"`$]", "", str(r).replace("\\", "/")) + "'" for r in roots
    )
    # Every value above is stripped of quotes, backticks and $ first: the text
    # goes inside a PowerShell string and a Windows Search SQL literal.
    return (
        f"SELECT TOP {limit} System.ItemPathDisplay FROM SYSTEMINDEX "  # noqa: S608
        f"WHERE ({scopes}) AND " + " AND ".join(clauses)
    )


def _search_index(sql: str) -> list[Path]:
    """Paths the Windows Search index has for ``sql``; empty when it cannot answer."""
    if os.name != "nt" or not sql:
        return []
    script = (
        "$ErrorActionPreference='Stop';"
        "$c=New-Object -ComObject ADODB.Connection;"
        "$c.Open(\"Provider=Search.CollatorDSO;Extended Properties='Application=Windows';\");"
        f"$r=$c.Execute(\"{sql}\");"
        "while(-not $r.EOF){[Console]::Out.WriteLine($r.Fields.Item(0).Value);$r.MoveNext()};"
        "$c.Close()"
    )
    try:
        done = subprocess.run(  # noqa: S603 — fixed program, the query is sanitised above
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_INDEX_TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):  # no index answer: the walk still runs
        log.debug("find_files: Windows Search index unavailable", exc_info=True)
        return []
    return [Path(line.strip()) for line in done.stdout.splitlines() if line.strip()]


def _walk(
    starts: list[Path],
    test: Any,
    kind: str,
    found: _Found,
    limit: int,
    deadline: float,
) -> None:
    """Breadth-first over ``starts``, shallow matches first, until done, full or out of time."""
    visited: set[str] = set()
    queue: collections.deque[tuple[Path, bool]] = collections.deque(
        (start, start.parent == start) for start in starts
    )
    while queue:
        folder, at_root = queue.popleft()
        key = os.path.normcase(str(folder))
        if key in visited:
            continue
        visited.add(key)
        found.folders += 1
        try:
            entries = list(os.scandir(folder))
        except OSError:  # a folder this user may not read: skipped like Explorer does
            continue
        for entry in entries:
            if time.monotonic() > deadline or len(found.paths) >= limit:
                return
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:  # vanished or unreadable entry
                continue
            if test(entry.name) and _kind_ok(is_dir, kind):
                found.add(Path(entry.path))
            low = entry.name.casefold()
            if (
                is_dir
                and low not in _SKIP_NAMES
                and not (at_root and low in _SKIP_AT_ROOT)
                and not entry.is_symlink()
            ):
                queue.append((Path(entry.path), False))
    found.finished = True


def _describe(path: Path) -> str:
    try:
        info = path.stat()
    except OSError:  # listed by the index but gone now
        return str(path)
    when = _dt.datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M")
    if path.is_dir():
        return f"{path}  (folder, modified {when})"
    size = info.st_size
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            shown = f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            break
        size /= 1024
    return f"{path}  ({shown}, modified {when})"


def search(
    query: str,
    *,
    root: str = "",
    kind: str = "any",
    extensions: list[str] | None = None,
    limit: int = _DEFAULT_LIMIT,
    budget_s: float = _WALK_BUDGET_S,
    index: Any = _search_index,
) -> str:
    """Blocking search; the text the model reads back."""
    extensions = list(extensions or [])
    roots = _roots(root)
    missing = [str(r) for r in roots if not r.exists()]
    if missing:
        raise ValueError(f"{missing[0]} does not exist on this computer.")
    test = matcher(query, extensions)
    found = _Found()
    started = time.monotonic()
    for path in index(_index_query(query, extensions, roots, limit)):
        if test(path.name) and path.exists() and _kind_ok(path.is_dir(), kind):
            found.add(path)
    found.from_index = len(found.paths)
    if len(found.paths) < limit:
        whole_drive = not root.strip()
        _walk(
            _start_points(roots, whole_drive), test, kind, found, limit, started + budget_s
        )
    took = time.monotonic() - started
    where = ", ".join(str(r) for r in roots)
    lines = [_describe(p) for p in found.paths[:limit]]
    if found.finished:
        tail = f"Searched all of {where} ({found.folders} folders) in {took:.0f} s."
    elif len(found.paths) >= limit:
        tail = (
            f"Showing the first {limit}; there may be more. "
            "Ask for a folder or a file type to narrow it."
        )
    else:
        tail = (
            f"Stopped after {took:.0f} s with {found.folders} folders of {where} searched; "
            "the rest of the drive was not. Name a folder to search it fully."
        )
    if not lines:
        return f"No file or folder named like {query!r} found. {tail}"
    return "\n".join([f"Found {len(lines)}:", *lines, tail])


class FindFilesTool:
    name: str = "find_files"
    risk_tier: str = "safe"
    description: str = (
        "Finds files and folders by name anywhere on this computer, the whole C: "
        "drive by default, user folders first. Use it for any 'find', 'where is', "
        "'search my PC/drive for' a file or folder. query: words of the name or a "
        "pattern like '*.pdf'; root: a folder or drive to search instead (e.g. "
        "'D:\\' or 'C:\\Users'); kind: file, folder or any; extensions: e.g. "
        "['pdf','docx']. Matches names, not contents."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Words of the name, or a pattern: *.mp4"},
            "root": {"type": "string", "description": "Folder or drive; empty = all of C:"},
            "kind": {"type": "string", "enum": list(_KINDS)},
            "extensions": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "minimum": 1, "maximum": _MAX_LIMIT},
        },
        "required": ["query"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        del ctx
        args = args or {}
        query = str(args.get("query") or "").strip()
        extensions = [str(e) for e in (args.get("extensions") or []) if str(e).strip()]
        if not query and not extensions:
            return ToolResult(success=False, output=None, error="Say what name to look for.")
        kind = str(args.get("kind") or "any")
        if kind not in _KINDS:
            kind = "any"
        try:
            limit = max(1, min(int(args.get("limit") or _DEFAULT_LIMIT), _MAX_LIMIT))
        except (TypeError, ValueError):
            limit = _DEFAULT_LIMIT
        try:
            output = await asyncio.to_thread(
                search,
                query,
                root=str(args.get("root") or ""),
                kind=kind,
                extensions=extensions,
                limit=limit,
            )
        except ValueError as exc:  # the reason goes back to the model
            return ToolResult(success=False, output=None, error=str(exc))
        except OSError as exc:
            log.warning("find_files failed", exc_info=True)
            return ToolResult(success=False, output=None, error=f"The search failed: {exc}")
        return ToolResult(success=True, output=output)


__all__ = ["FindFilesTool", "matcher", "search"]
