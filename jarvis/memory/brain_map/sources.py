"""Readers over the stores the brain map indexes. Read-only, every one.

Each reader turns one existing store into :class:`Record` s: one per source
row (a chat, a mission, a quest, an IDE project...), carrying the row's
identity, its status mapped by :mod:`.status`, and — for a conversation —
only the messages after the cursor the indexer stored last time. Nothing is
written to any of these stores; SQLite files are opened ``mode=ro``.

Which copy of a conversation counts (audit §1, duplication): a spoken turn is
mirrored into the agent-chat store (``AC/voice_mirror.py``), so voice is read
from there, never again from ``sessions.db``, ``chats.db`` or Hermes's own
``state.db``. Hermes and the coding CLIs contribute their SESSIONS (cwd, last
activity, agent) through the cost layer's existing incremental index
(``jarvis/costs/cli_usage_index.py`` → ``cli_usage_index.db``), which already
reads Hermes's ``state.db`` read-only with a cursor; its message bodies are not
read here.

A reader that fails reports the failure and yields nothing; it never raises
into the indexer, and its absence never deletes what an earlier run derived
(``present`` is only reported for a store that was actually listed).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from .constants import DAY_MS
from .status import (
    Verdict,
    automation_status,
    conversation_status,
    goal_status,
    ide_project_status,
    mission_status,
    output_status,
    quest_status,
    thread_status,
    wiki_project_status,
)

log = logging.getLogger(__name__)

#: Coding-CLI sessions older than this are not projected (they stay in the
#: usage index; the map is about what is still going on).
CLI_SESSION_HORIZON_MS = 365 * DAY_MS
#: Finished automations older than this are not projected.
AUTOMATION_HORIZON_MS = 30 * DAY_MS


@dataclass(frozen=True, slots=True)
class Message:
    """One text unit of a conversation, pointing back at its row."""

    message_ref: str  # "seq:12" (agent chat), "turn:3" (imported page)
    message_id: str
    role: str  # user | assistant
    text: str
    ts_ms: int


@dataclass(frozen=True, slots=True)
class Link:
    """An explicit edge from this record to another source row."""

    kind: str
    target_kind: str
    target_id: str
    confidence: float = 1.0
    #: True: the edge points FROM the target TO this record (mission produced output).
    reverse: bool = False


@dataclass(slots=True)
class Record:
    source_kind: str
    source_id: str
    entity_kind: str
    title: str
    verdict: Verdict
    native_status: str = ""
    last_activity_ms: int = 0
    version: str = ""
    cwd: str = ""
    locator: str = ""
    #: An explicit project: the folder it ran in, or a wiki project's slug.
    project_path: str = ""
    project_name: str = ""
    #: This record IS the project (an IDE project, a wiki project page).
    defines_project: bool = False
    links: list[Link] = field(default_factory=list)
    #: Short text matched against known project names. Never stored.
    terms_text: str = ""
    branch: str = ""
    agent_name: str = ""
    user_priority: int = 0
    due_ms: int | None = None
    #: Conversations: the messages after the stored cursor (None = unchanged).
    messages: list[Message] | None = None
    #: True: ``messages`` is the source's whole current content, so segments
    #: of this source that are not in it any more were deleted at the source.
    full_messages: bool = False
    cursor: str = ""


@dataclass(frozen=True, slots=True)
class Stored:
    """What the indexer remembers about one source row."""

    cursor: str
    version_hash: str
    last_activity_ms: int
    native_status: str


@dataclass(slots=True)
class ReadContext:
    now_ms: int
    stored: Callable[[str, str], Stored | None]
    known_ids: Callable[[str], set[str]]


@dataclass(slots=True)
class SourceBatch:
    records: list[Record] = field(default_factory=list)
    #: Every id that currently exists, per source kind fully listed this run.
    present: dict[str, set[str]] = field(default_factory=dict)
    errors: int = 0


class SourceReader(Protocol):
    name: str

    def read(self, ctx: ReadContext) -> SourceBatch: ...


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ro(path: Path | None) -> sqlite3.Connection | None:
    """Read-only connection, ``None`` when the file is absent or unreadable."""
    if path is None or not path.is_file():
        return None
    try:
        # Percent-encoded: a space or '#' in an unencoded URI makes SQLite open
        # a different, empty database (cli_usage_index._open_ro documents it).
        uri = f"file:{quote(path.as_posix(), safe='/:')}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:  # unreadable store: logged, read as absent
        log.warning("brain map: %s not readable (%s)", path.name, type(exc).__name__)
        return None


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _first_line(text: str, limit: int = 120) -> str:
    line = " ".join((text or "").split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):  # a non-numeric column value counts as 0
        return 0


def _payload(raw: Any) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):  # a malformed event row carries nothing usable
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# agent chat (+ voice turns mirrored there, + /goal)
# ---------------------------------------------------------------------------

#: Surfaces whose chats are conversations of the person's. ``local-models`` is
#: the setup assistant, not a conversation about anything.
_CHAT_SURFACES = ("jarvis", "agent", "society")


class AgentChatReader:
    name = "agent_chat"

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def read(self, ctx: ReadContext) -> SourceBatch:
        batch = SourceBatch()
        conn = _ro(self.db_path)
        if conn is None:
            return batch
        try:
            if not _has_table(conn, "agent_chat_sessions"):
                return batch
            sessions = conn.execute(
                "SELECT session_id, title, cwd, surface, created_ms, updated_ms, preview"
                " FROM agent_chat_sessions WHERE surface IN (?, ?, ?)",
                _CHAT_SURFACES,
            ).fetchall()
            batch.present["agent_chat"] = {str(r["session_id"]) for r in sessions}
            for row in sessions:
                batch.records.append(self._session(conn, row, ctx))
            if _has_table(conn, "agent_chat_controls"):
                batch.present["goal"] = set()
                for row in conn.execute("SELECT session_id, state_json FROM agent_chat_controls"):
                    record = self._goal(row, ctx)
                    if record is not None:
                        batch.present["goal"].add(record.source_id)
                        batch.records.append(record)
        except sqlite3.Error as exc:  # logged; nothing from this store this run
            log.warning("brain map: agent chat read failed (%s)", type(exc).__name__)
            batch.errors += 1
            batch.present.clear()
        finally:
            conn.close()
        return batch

    def _session(self, conn: sqlite3.Connection, row: sqlite3.Row, ctx: ReadContext) -> Record:
        sid = str(row["session_id"])
        updated = _int(row["updated_ms"])
        stored = ctx.stored("agent_chat", sid)
        native = stored.native_status if stored else "ok"
        messages: list[Message] | None = None
        cursor = stored.cursor if stored else "0"
        if stored is None or stored.last_activity_ms != updated:
            messages = []
            after = _int(cursor)
            pending: set[str] = (
                set(native.split(":", 1)[1].split(",")) if native.startswith("approval:") else set()
            )
            failed = native == "failed"
            for ev in conn.execute(
                "SELECT seq, ts_ms, kind, payload FROM agent_chat_events WHERE session_id=?"
                " AND seq > ? AND kind IN ('user_message', 'assistant_text', 'turn_finished',"
                " 'approval_required', 'approval_resolved') ORDER BY seq",
                (sid, after),
            ):
                seq = _int(ev["seq"])
                after = max(after, seq)
                payload = _payload(ev["payload"])
                kind = ev["kind"]
                if kind in ("user_message", "assistant_text"):
                    text = str(payload.get("text") or "")
                    if text.strip():
                        messages.append(
                            Message(
                                message_ref=f"seq:{seq}",
                                message_id=str(
                                    payload.get("message_id") or payload.get("turn_id") or ""
                                ),
                                role="user" if kind == "user_message" else "assistant",
                                text=text,
                                ts_ms=_int(ev["ts_ms"]),
                            )
                        )
                elif kind == "turn_finished":
                    failed = str(payload.get("status") or "") == "error"
                elif kind == "approval_required":
                    pending.add(str(payload.get("approval_id") or "?"))
                elif kind == "approval_resolved":
                    pending.discard(str(payload.get("approval_id") or "?"))
            cursor = str(after)
            pending.discard("")
            native = (
                f"approval:{','.join(sorted(pending))}"
                if pending
                else ("failed" if failed else "ok")
            )
        verdict = conversation_status(
            last_activity_ms=updated,
            last_turn_failed=native == "failed",
            awaiting_approval=native.startswith("approval:"),
            now_ms=ctx.now_ms,
        )
        cwd = str(row["cwd"] or "")
        return Record(
            source_kind="agent_chat",
            source_id=sid,
            entity_kind="CONVERSATION",
            title=_first_line(str(row["title"] or row["preview"] or "Conversation")),
            verdict=verdict,
            native_status=native,
            last_activity_ms=updated,
            version=f"{updated}",
            cwd=cwd,
            project_path=cwd,
            messages=messages,
            cursor=cursor,
        )

    def _goal(self, row: sqlite3.Row, ctx: ReadContext) -> Record | None:
        state = _payload(row["state_json"])
        goal = state.get("goal")
        if not isinstance(goal, dict) or not goal.get("objective"):
            return None
        sid = str(row["session_id"])
        status = str(goal.get("status") or "")
        updated = _int(goal.get("updated_ms"))
        return Record(
            source_kind="goal",
            source_id=f"{sid}:{goal.get('id') or 'goal'}",
            entity_kind="TASK",
            title=_first_line(f"Goal: {goal.get('objective')}"),
            verdict=goal_status(status, updated, ctx.now_ms),
            native_status=status,
            last_activity_ms=updated,
            version=f"{status}:{updated}",
            links=[Link("part_of", "agent_chat", sid)],
        )


# ---------------------------------------------------------------------------
# missions + their outputs
# ---------------------------------------------------------------------------


class MissionReader:
    name = "mission"

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def read(self, ctx: ReadContext) -> SourceBatch:
        batch = SourceBatch()
        conn = _ro(self.db_path)
        if conn is None:
            return batch
        try:
            if not _has_table(conn, "missions"):
                return batch
            cols = {r[1] for r in conn.execute("PRAGMA table_info(missions)")}
            beat = "last_heartbeat_ms" if "last_heartbeat_ms" in cols else "0"
            rows = conn.execute(
                f"SELECT id, prompt, state, created_ms, updated_ms, {beat} AS beat FROM missions"  # noqa: S608 — only '?' placeholders and fixed column names are interpolated
            ).fetchall()
            batch.present["mission"] = {str(r["id"]) for r in rows}
            for row in rows:
                state = str(row["state"] or "")
                last = max(_int(row["updated_ms"]), _int(row["beat"]))
                prompt = str(row["prompt"] or "")
                batch.records.append(
                    Record(
                        source_kind="mission",
                        source_id=str(row["id"]),
                        entity_kind="TASK",
                        title=_first_line(prompt or "Mission"),
                        verdict=mission_status(state, last, ctx.now_ms),
                        native_status=state,
                        last_activity_ms=last,
                        version=f"{state}:{last}",
                        terms_text=prompt[:1000],
                    )
                )
        except sqlite3.Error as exc:  # logged; nothing from this store this run
            log.warning("brain map: missions read failed (%s)", type(exc).__name__)
            batch.errors += 1
            batch.present.clear()
        finally:
            conn.close()
        return batch


class OutputReader:
    """Persistent ``mission_<short>`` output folders (``outputs_routes``)."""

    name = "output"

    def __init__(self, roots: Sequence[Path], missions_db: Path) -> None:
        self.roots = tuple(roots)
        self.missions_db = missions_db

    def read(self, ctx: ReadContext) -> SourceBatch:
        batch = SourceBatch()
        missions: list[tuple[str, str, str]] = []
        conn = _ro(self.missions_db)
        if conn is not None:
            try:
                if _has_table(conn, "missions"):
                    missions = [
                        (str(r["id"]), str(r["state"] or ""), str(r["prompt"] or ""))
                        for r in conn.execute("SELECT id, state, prompt FROM missions")
                    ]
            except sqlite3.Error as exc:  # logged; outputs are listed without their missions
                log.warning("brain map: missions read failed (%s)", type(exc).__name__)
                batch.errors += 1
            finally:
                conn.close()
        listed = False
        present: set[str] = set()
        for root in self.roots:
            try:
                entries = [
                    p for p in root.iterdir() if p.is_dir() and p.name.startswith("mission_")
                ]
            except OSError as exc:  # an absent or unreadable root: nothing from it
                log.debug("brain map: outputs root unreadable (%s)", type(exc).__name__)
                continue
            listed = True
            for entry in entries:
                if entry.name in present:
                    continue  # roots are canonical-first; the first copy wins
                present.add(entry.name)
                short = entry.name[len("mission_") :]
                match = next(
                    (m for m in missions if len(short) >= 6 and m[0].startswith(short)), None
                )
                try:
                    mtime = int(entry.stat().st_mtime * 1000)
                except OSError:  # vanished between listing and stat: skip it this run
                    continue
                links = [Link("produced", "mission", match[0], reverse=True)] if match else []
                batch.records.append(
                    Record(
                        source_kind="output",
                        source_id=entry.name,
                        entity_kind="OUTPUT",
                        title=_first_line(f"Output: {match[2]}" if match else entry.name),
                        verdict=output_status(match[1] if match else None, mtime, ctx.now_ms),
                        native_status=match[1] if match else "",
                        last_activity_ms=mtime,
                        version=f"{mtime}:{match[1] if match else ''}",
                        locator=str(entry),
                        links=links,
                    )
                )
        if listed:
            batch.present["output"] = present
        return batch


# ---------------------------------------------------------------------------
# society quests
# ---------------------------------------------------------------------------


class QuestReader:
    name = "quest"

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def read(self, ctx: ReadContext) -> SourceBatch:
        batch = SourceBatch()
        conn = _ro(self.db_path)
        if conn is None:
            return batch
        try:
            if not _has_table(conn, "society_quests"):
                return batch
            names: dict[str, str] = {}
            if _has_table(conn, "society_agents"):
                names = {
                    str(r["agent_id"]): str(r["name"])
                    for r in conn.execute("SELECT agent_id, name FROM society_agents")
                }
            rows = conn.execute(
                "SELECT quest_id, title, text, state, agent_id, updated_ms FROM society_quests"
            ).fetchall()
            batch.present["quest"] = {str(r["quest_id"]) for r in rows}
            for row in rows:
                state = str(row["state"] or "")
                updated = _int(row["updated_ms"])
                batch.records.append(
                    Record(
                        source_kind="quest",
                        source_id=str(row["quest_id"]),
                        entity_kind="TASK",
                        title=_first_line(str(row["title"] or "Quest")),
                        verdict=quest_status(state, updated, ctx.now_ms),
                        native_status=state,
                        last_activity_ms=updated,
                        version=f"{state}:{updated}",
                        terms_text=f"{row['title'] or ''} {str(row['text'] or '')[:500]}",
                        agent_name=names.get(str(row["agent_id"] or ""), ""),
                    )
                )
        except sqlite3.Error as exc:  # logged; nothing from this store this run
            log.warning("brain map: quests read failed (%s)", type(exc).__name__)
            batch.errors += 1
            batch.present.clear()
        finally:
            conn.close()
        return batch


# ---------------------------------------------------------------------------
# automations / reminders (tasks table in jarvis.db)
# ---------------------------------------------------------------------------


class AutomationReader:
    name = "automation"

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def read(self, ctx: ReadContext) -> SourceBatch:
        batch = SourceBatch()
        conn = _ro(self.db_path)
        if conn is None:
            return batch
        try:
            if not _has_table(conn, "tasks"):
                return batch
            rows = conn.execute(
                "SELECT id, title, state, due_at_ns, created_at_ns, started_at_ns,"
                " finished_at_ns FROM tasks"
            ).fetchall()
            present: set[str] = set()
            for row in rows:
                state = str(row["state"] or "")
                stamps = [
                    _int(row["finished_at_ns"]),
                    _int(row["started_at_ns"]),
                    _int(row["created_at_ns"]),
                ]
                updated = max(stamps) // 1_000_000
                if (
                    state in ("completed", "cancelled")
                    and ctx.now_ms - updated > AUTOMATION_HORIZON_MS
                ):
                    continue
                due = _int(row["due_at_ns"]) // 1_000_000 or None
                present.add(str(row["id"]))
                batch.records.append(
                    Record(
                        source_kind="automation",
                        source_id=str(row["id"]),
                        entity_kind="TASK",
                        title=_first_line(str(row["title"] or "Automation")),
                        verdict=automation_status(state, due, updated, ctx.now_ms),
                        native_status=state,
                        last_activity_ms=updated,
                        version=f"{state}:{updated}:{due}",
                        due_ms=due,
                        terms_text=str(row["title"] or ""),
                    )
                )
            batch.present["automation"] = present
        except sqlite3.Error as exc:  # logged; nothing from this store this run
            log.warning("brain map: automations read failed (%s)", type(exc).__name__)
            batch.errors += 1
            batch.present.clear()
        finally:
            conn.close()
        return batch


# ---------------------------------------------------------------------------
# Agentic IDE library
# ---------------------------------------------------------------------------


def git_branch(path: str) -> str:
    """The checked-out branch of a folder, read from ``.git/HEAD`` (no subprocess)."""
    if not path:
        return ""
    head = Path(path) / ".git"
    try:
        if head.is_file():  # a linked worktree: ".git" is "gitdir: <path>"
            pointer = head.read_text(encoding="utf-8", errors="replace").strip()
            if not pointer.startswith("gitdir:"):
                return ""
            head = Path(pointer.split(":", 1)[1].strip())
        text = (head / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:  # not a git checkout, or not reachable right now
        return ""
    prefix = "ref: refs/heads/"
    return text[len(prefix) :] if text.startswith(prefix) else ""


class IdeLibraryReader:
    name = "ide"

    def __init__(
        self,
        list_projects: Callable[[], Iterable[Any]] | None = None,
        list_threads: Callable[[str], Iterable[Any]] | None = None,
        branch_of: Callable[[str], str] = git_branch,
    ) -> None:
        self._list_projects = list_projects
        self._list_threads = list_threads
        self._branch_of = branch_of

    def _defaults(self) -> tuple[Callable[[], Iterable[Any]], Callable[[str], Iterable[Any]]]:
        from jarvis.agentic_ide import library

        return (
            self._list_projects or (lambda: library.list_projects(include_archived=True)),
            self._list_threads or (lambda pid: library.list_threads(pid, include_archived=True)),
        )

    def read(self, ctx: ReadContext) -> SourceBatch:
        batch = SourceBatch()
        list_projects, list_threads = self._defaults()
        try:
            projects = list(list_projects())
        except Exception as exc:  # noqa: BLE001 — a broken library file is no projects this run
            log.warning("brain map: IDE library read failed (%s)", type(exc).__name__)
            batch.errors += 1
            return batch
        batch.present["ide_project"] = set()
        batch.present["ide_thread"] = set()
        for project in projects:
            if getattr(project, "scratch", False):
                continue
            pid = str(project.id)
            path = str(project.path or "")
            opened = int(float(getattr(project, "last_opened_at", 0) or 0) * 1000)
            archived = bool(getattr(project, "archived", False))
            branch = self._branch_of(path)
            batch.present["ide_project"].add(pid)
            batch.records.append(
                Record(
                    source_kind="ide_project",
                    source_id=pid,
                    entity_kind="PROJECT",
                    title=_first_line(str(project.name or Path(path).name or "Project")),
                    verdict=ide_project_status(archived, opened, ctx.now_ms),
                    native_status="archived" if archived else "open",
                    last_activity_ms=opened,
                    version=f"{opened}:{archived}:{branch}:{project.name}",
                    cwd=path,
                    locator=path,
                    project_path=path,
                    defines_project=True,
                    branch=branch,
                )
            )
            try:
                threads = list(list_threads(pid))
            except Exception as exc:  # noqa: BLE001 — one unreadable chat list costs that project only
                log.warning("brain map: IDE threads read failed (%s)", type(exc).__name__)
                batch.errors += 1
                batch.present.pop("ide_thread", None)
                continue
            for thread in threads:
                tid = str(thread.id)
                updated = int(float(getattr(thread, "updated_at", 0) or 0) * 1000)
                t_archived = bool(getattr(thread, "archived", False))
                if "ide_thread" in batch.present:
                    batch.present["ide_thread"].add(tid)
                agent = str(getattr(thread, "agent", "") or "")
                batch.records.append(
                    Record(
                        source_kind="ide_thread",
                        source_id=tid,
                        entity_kind="THREAD",
                        title=_first_line(str(thread.title or f"{agent or 'Agent'} chat")),
                        verdict=thread_status("IDE chat", t_archived, updated, ctx.now_ms),
                        native_status="archived" if t_archived else "open",
                        last_activity_ms=updated,
                        version=f"{updated}:{t_archived}:{thread.title}",
                        cwd=path,
                        project_path=path,
                        agent_name=agent,
                    )
                )
        return batch


# ---------------------------------------------------------------------------
# coding-CLI + Hermes sessions, from the cost layer's incremental index
# ---------------------------------------------------------------------------


class CliSessionReader:
    name = "cli_session"

    def __init__(self, index_db: Path) -> None:
        self.index_db = index_db

    def read(self, ctx: ReadContext) -> SourceBatch:
        batch = SourceBatch()
        conn = _ro(self.index_db)
        if conn is None:
            return batch
        try:
            if not _has_table(conn, "cli_turns"):
                return batch
            since = ctx.now_ms - CLI_SESSION_HORIZON_MS
            rows = conn.execute(
                "SELECT agent, session_id, MAX(ts_ms) AS last, COUNT(*) AS turns,"
                " MAX(cwd) AS cwd, MAX(label) AS label FROM cli_turns"
                " WHERE session_id <> '' GROUP BY agent, session_id HAVING MAX(ts_ms) >= ?",
                (since,),
            ).fetchall()
            present: set[str] = set()
            for row in rows:
                agent = str(row["agent"] or "")
                sid = f"{agent}:{row['session_id']}"
                last = _int(row["last"])
                cwd = str(row["cwd"] or "")
                where = str(row["label"] or "") or (Path(cwd).name if cwd else "")
                present.add(sid)
                batch.records.append(
                    Record(
                        source_kind="cli_session",
                        source_id=sid,
                        entity_kind="THREAD",
                        title=_first_line(f"{agent} session" + (f" in {where}" if where else "")),
                        verdict=thread_status("coding session", False, last, ctx.now_ms),
                        native_status="",
                        last_activity_ms=last,
                        version=f"{last}:{row['turns']}",
                        cwd=cwd,
                        project_path=cwd,
                        agent_name=agent,
                    )
                )
            batch.present["cli_session"] = present
        except sqlite3.Error as exc:  # logged; nothing from this store this run
            log.warning("brain map: CLI usage index read failed (%s)", type(exc).__name__)
            batch.errors += 1
            batch.present.clear()
        finally:
            conn.close()
        return batch


# ---------------------------------------------------------------------------
# wiki: project pages and imported conversations
# ---------------------------------------------------------------------------

_PRIORITY_WORDS = {"urgent": 3, "critical": 3, "high": 2, "medium": 1, "normal": 1, "low": 0}


def _priority(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, min(3, value))
    return _PRIORITY_WORDS.get(str(value or "").strip().lower(), 0)


class WikiProjectReader:
    """``<vault>/projects/*.md`` pages (``W/page.py`` type ``project``)."""

    name = "wiki_project"

    def __init__(self, vault_root: Path) -> None:
        self.vault_root = vault_root

    def read(self, ctx: ReadContext) -> SourceBatch:
        from jarvis.memory.frontmatter import parse_frontmatter

        batch = SourceBatch()
        folder = self.vault_root / "projects"
        try:
            pages = sorted(p for p in folder.glob("*.md") if p.is_file())
        except OSError as exc:  # no vault, or not reachable right now
            log.debug("brain map: wiki projects unreadable (%s)", type(exc).__name__)
            return batch
        if not folder.is_dir():
            return batch
        present: set[str] = set()
        for page in pages:
            rel = page.relative_to(self.vault_root).as_posix()
            try:
                stat = page.stat()
                meta, _body = parse_frontmatter(page.read_text(encoding="utf-8", errors="replace"))
            except OSError:  # vanished or locked mid-read: picked up next run
                batch.errors += 1
                continue
            present.add(rel)
            mtime = int(stat.st_mtime * 1000)
            status = str(meta.get("status") or "")
            slug = str(meta.get("slug") or page.stem)
            batch.records.append(
                Record(
                    source_kind="wiki_project",
                    source_id=rel,
                    entity_kind="PROJECT",
                    title=_first_line(str(meta.get("title") or page.stem)),
                    verdict=wiki_project_status(status, mtime, ctx.now_ms),
                    native_status=status,
                    last_activity_ms=mtime,
                    version=f"{stat.st_mtime_ns}:{stat.st_size}",
                    locator=rel,
                    project_name=slug,
                    defines_project=True,
                    user_priority=_priority(meta.get("priority")),
                )
            )
        batch.present["wiki_project"] = present
        return batch


#: The project every imported chat belongs to: an export has no projects of
#: its own, and a conversation outside every project never reaches the map.
IMPORTED_CHATS_PROJECT = "Imported chats"


class WikiImportReader:
    """Conversations imported from ChatGPT / Claude exports (``W/importer.py``).

    One record per conversation, keyed ``<page path>#<conversation id>``. Pages
    written before the importer added markers are read as one conversation
    each, keyed by their path. A
    page whose size and mtime did not change is not opened again; a changed
    page is parsed in full and the indexer's content hashes decide which turns
    actually changed.
    """

    name = "wiki_import"

    def __init__(self, vault_root: Path) -> None:
        self.vault_root = vault_root

    def read(self, ctx: ReadContext) -> SourceBatch:
        from jarvis.memory.wiki.import_formats import (
            parse_conversation_markers,
            parse_legacy_conversation_page,
        )

        batch = SourceBatch()
        folder = self.vault_root / "imports"
        if not folder.is_dir():
            return batch
        known = ctx.known_ids("wiki_import")
        present: set[str] = set()
        try:
            pages = sorted(folder.rglob("*.md"))
        except OSError as exc:  # unreadable import folder: nothing from it this run
            log.debug("brain map: wiki imports unreadable (%s)", type(exc).__name__)
            return batch
        for page in pages:
            rel = page.relative_to(self.vault_root).as_posix()
            try:
                stat = page.stat()
            except OSError:  # vanished between listing and stat
                continue
            version = f"{stat.st_mtime_ns}:{stat.st_size}"
            prefix = f"{rel}#"
            previous = {sid for sid in known if sid.startswith(prefix)}
            sample = next(iter(previous), None)
            stored = ctx.stored("wiki_import", sample) if sample else None
            if stored is not None and stored.version_hash.startswith(version + "|"):
                present |= previous  # unchanged page: its conversations still exist
                continue
            try:
                text = page.read_text(encoding="utf-8", errors="replace")
            except OSError:  # locked mid-read: picked up next run
                batch.errors += 1
                present |= previous
                continue
            turns = parse_conversation_markers(text) or parse_legacy_conversation_page(
                text, "page-" + hashlib.sha256(rel.encode("utf-8")).hexdigest()[:12]
            )
            grouped: dict[str, list[Any]] = {}
            for turn in turns:
                grouped.setdefault(turn.conversation_id, []).append(turn)
            mtime = int(stat.st_mtime * 1000)
            for conv_id, conv_turns in grouped.items():
                sid = f"{rel}#{conv_id}"
                present.add(sid)
                last = max((t.ts_ms or 0 for t in conv_turns), default=0) or (
                    conv_turns[0].conversation_created_ms or mtime
                )
                messages = [
                    Message(
                        message_ref=f"turn:{t.index}",
                        message_id=t.message_id,
                        role="user" if t.role.lower() in ("you", "user") else "assistant",
                        text=t.text,
                        ts_ms=t.ts_ms or t.conversation_created_ms or mtime,
                    )
                    for t in conv_turns
                ]
                batch.records.append(
                    Record(
                        source_kind="wiki_import",
                        source_id=sid,
                        entity_kind="CONVERSATION",
                        title=_first_line(conv_turns[0].title or "Imported conversation"),
                        verdict=thread_status("imported conversation", False, last, ctx.now_ms),
                        native_status="",
                        last_activity_ms=last,
                        version=version,
                        locator=rel,
                        project_name=IMPORTED_CHATS_PROJECT,
                        messages=messages,
                        full_messages=True,
                        cursor=str(len(messages)),
                    )
                )
        batch.present["wiki_import"] = present
        return batch


# ---------------------------------------------------------------------------
# default wiring
# ---------------------------------------------------------------------------


def default_readers(
    *,
    data_dir: Path,
    vault_root: Path | None,
    outputs_roots: Sequence[Path] = (),
) -> list[SourceReader]:
    """The production reader set over one data directory.

    Order matters for one thing: project-defining readers (IDE, CLI cwd) run
    before the wiki project pages, so a wiki page about an IDE project attaches
    to it instead of starting a second project.
    """
    from jarvis.costs.cli_usage_index import index_db_path

    readers: list[SourceReader] = [
        IdeLibraryReader(),
        CliSessionReader(index_db_path(data_dir)),
        AgentChatReader(data_dir / "agent_chat.db"),
        MissionReader(data_dir / "missions.db"),
        OutputReader(outputs_roots, data_dir / "missions.db"),
        QuestReader(data_dir / "society.db"),
        AutomationReader(data_dir / "jarvis.db"),
    ]
    if vault_root is not None:
        readers += [WikiProjectReader(vault_root), WikiImportReader(vault_root)]
    return readers


def conversation_id_of(source_kind: str, source_id: str) -> str:
    """The conversation's own id inside a composite source id.

    ``wiki_import`` rows are ``<page>#<export conversation id>``; ``cli_session``
    rows are ``<agent>:<session id>``; every other source id is the id itself.
    """
    if source_kind == "wiki_import" and "#" in source_id:
        return source_id.split("#", 1)[1]
    if source_kind == "cli_session" and ":" in source_id:
        return source_id.split(":", 1)[1]
    return source_id


def home_dir() -> str:
    """The user's home folder: a cwd there is no project."""
    return os.path.normcase(str(Path.home().resolve()))


__all__ = [
    "AgentChatReader",
    "AutomationReader",
    "CliSessionReader",
    "IdeLibraryReader",
    "Link",
    "Message",
    "MissionReader",
    "OutputReader",
    "QuestReader",
    "ReadContext",
    "Record",
    "SourceBatch",
    "SourceReader",
    "Stored",
    "WikiImportReader",
    "WikiProjectReader",
    "conversation_id_of",
    "default_readers",
    "git_branch",
    "home_dir",
]
