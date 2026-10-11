"""The incremental indexer: normalize → extract → classify → link → update.

One :meth:`BrainMapIndexer.run` does, in order:

1. **Read** every source through its reader (read-only, outside any write
   transaction). Native rows come back whole — those stores are small — and
   conversations only with the messages after their stored cursor.
2. **Project** each record onto its entity (status from ``status.py``),
   resolve its project (explicit folder / wiki slug / link), and add its
   explicit edges.
3. **Segment + hash** each new message: ``segment_id`` is the message's
   identity, ``content_hash`` its normalized text. A segment whose hash and
   rules version are unchanged is skipped — re-reading a page or a cursor
   reset costs no extraction.
4. **Extract** with the deterministic rules (and, on demand only, a model
   proposal step behind ``extractor.Extractor``), store each entity with its
   verbatim evidence, close blockers / tasks that a later message resolves.
5. **Cluster**: messages and native texts that name a known project or one of
   its branches link their conversation to it, with a confidence; below the
   floor the link is only a suggestion.
6. **Re-derive** entities whose time-based status is due (stale, overdue).
7. **Recompute** only the projects whose cluster changed: status rollup,
   summary, priorities.

Every write is a short transaction of its own: ``jarvis.db`` is shared with
the recall log, the task store and the wiki journal, and a long-held write
lock would stall them.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import rules
from .constants import CONFIDENCE_FLOOR, RULES_VERSION
from .engine import members_of, open_blockers, own_verdict, priority, projects_of, rollup
from .extractor import Extractor, NoopExtractor, SegmentText, validate
from .sources import (
    Message,
    ReadContext,
    Record,
    SourceReader,
    Stored,
    conversation_id_of,
    home_dir,
)
from .status import Verdict, day, extracted_status
from .store import BrainMapStore

log = logging.getLogger(__name__)

#: How many segments one on-demand run hands the model step, at most.
MODEL_SEGMENT_BUDGET = 50
MODEL_BATCH = 10

#: Folder names too generic to cluster on.
_GENERIC_NAMES = frozenset(
    {
        "app",
        "apps",
        "code",
        "data",
        "demo",
        "dev",
        "docs",
        "home",
        "jarvis",
        "main",
        "new",
        "project",
        "projects",
        "repo",
        "repos",
        "src",
        "temp",
        "test",
        "tests",
        "tmp",
        "work",
        "workspace",
        "desktop",
        "documents",
        "downloads",
    }
)
_WORDS = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "the a an and or to of for on in at is are was we i it this that with be by from"
    " just now then our my your next step steps done finished shipped blocked need"
    " todo".split()
)


def _h(value: str, n: int = 16) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:n]  # noqa: S324 — an id, not security


def primary_id(source_kind: str, source_id: str) -> str:
    return f"{source_kind}:{_h(source_id)}"


def project_key_for_path(path: str) -> str:
    try:
        resolved = str(Path(path).expanduser().resolve())
    except (OSError, ValueError):  # unresolvable: the literal path is the key
        resolved = path
    return "path:" + os.path.normcase(resolved.rstrip("/\\") or resolved)


def project_id_for_key(key: str) -> str:
    return f"project:{_h(key)}"


def slug(text: str) -> str:
    return "-".join(_WORDS.findall((text or "").lower()))


def _norm_title(title: str) -> str:
    return " ".join(_WORDS.findall(title.lower()))[:80]


def content_hash(text: str) -> str:
    normalized = " ".join((text or "").split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _tokens(text: str) -> set[str]:
    return {w for w in _WORDS.findall(text.lower()) if len(w) >= 4 and w not in _STOP}


@dataclass(slots=True)
class RunStats:
    trigger: str = ""
    sources_seen: int = 0
    sources_changed: int = 0
    segments_seen: int = 0
    segments_processed: int = 0
    entities_changed: int = 0
    projects_recomputed: int = 0
    errors: int = 0
    duration_ms: int = 0

    def to_dict(self) -> dict[str, int | str]:
        return {
            "trigger": self.trigger,
            "sources_seen": self.sources_seen,
            "sources_changed": self.sources_changed,
            "segments_seen": self.segments_seen,
            "segments_processed": self.segments_processed,
            "entities_changed": self.entities_changed,
            "projects_recomputed": self.projects_recomputed,
            "errors": self.errors,
            "duration_ms": self.duration_ms,
        }


@dataclass(slots=True)
class _ProjectName:
    project_id: str
    slug: str
    pattern: re.Pattern[str]


@dataclass(slots=True)
class _RunState:
    now: int
    stats: RunStats
    dirty: set[str] = field(default_factory=set)
    dirty_projects: set[str] = field(default_factory=set)
    names: list[_ProjectName] = field(default_factory=list)
    branches: dict[str, str] = field(default_factory=dict)
    model_segments: list[tuple[SegmentText, Record, Message]] = field(default_factory=list)


class BrainMapIndexer:
    """Runs the pipeline; one run at a time per instance."""

    def __init__(
        self,
        store: BrainMapStore,
        readers: Sequence[SourceReader] | Callable[[], Sequence[SourceReader]],
        *,
        extractor: Extractor | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self._readers = readers
        self.extractor: Extractor = extractor or NoopExtractor()
        self._clock = clock
        self._run_lock = threading.Lock()
        self._home = home_dir()

    def now_ms(self) -> int:
        return int(self._clock() * 1000)

    @property
    def busy(self) -> bool:
        return self._run_lock.locked()

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------

    def run(self, *, trigger: str = "manual", use_model: bool = False) -> RunStats:
        """One incremental pass. ``use_model`` is honoured only off the tick."""
        with self._run_lock:
            started = time.monotonic()
            state = _RunState(now=self.now_ms(), stats=RunStats(trigger=trigger))
            with self.store.write() as conn:
                run_id = self.store.start_run(conn, trigger, state.now)
                self._load_names(conn, state)
            readers = self._readers() if callable(self._readers) else self._readers
            for reader in readers:
                self._run_reader(reader, state)
            if use_model and trigger != "tick":
                self._model_step(state)
            self._review_due(state)
            self._recompute(state)
            state.stats.duration_ms = int((time.monotonic() - started) * 1000)
            with self.store.write() as conn:
                self.store.finish_run(
                    conn,
                    run_id,
                    finished_ms=self.now_ms(),
                    sources_seen=state.stats.sources_seen,
                    sources_changed=state.stats.sources_changed,
                    segments_seen=state.stats.segments_seen,
                    segments_processed=state.stats.segments_processed,
                    entities_changed=state.stats.entities_changed,
                    projects_recomputed=state.stats.projects_recomputed,
                    errors=state.stats.errors,
                )
            # Counts only: never a title or a quote (privacy, AP-34 spirit).
            log.info(
                "brain map: %s run, %d/%d sources changed, %d/%d segments processed,"
                " %d projects recomputed, %d errors",
                trigger,
                state.stats.sources_changed,
                state.stats.sources_seen,
                state.stats.segments_processed,
                state.stats.segments_seen,
                state.stats.projects_recomputed,
                state.stats.errors,
            )
            return state.stats

    # ------------------------------------------------------------------
    # reading
    # ------------------------------------------------------------------

    def _context(self, state: _RunState) -> ReadContext:
        def stored(kind: str, source_id: str) -> Stored | None:
            with self.store.read() as conn:
                row = self.store.get_source(conn, kind, source_id)
                if row is None:
                    return None
                ent = conn.execute(
                    "SELECT native_status FROM brain_map_entities WHERE source_kind=?"
                    " AND source_id=? AND origin <> 'extracted' LIMIT 1",
                    (kind, source_id),
                ).fetchone()
            return Stored(
                cursor=str(row["cursor"]),
                version_hash=str(row["version_hash"]),
                last_activity_ms=int(row["last_activity_ms"]),
                native_status=str(ent[0]) if ent is not None else "",
            )

        def known_ids(kind: str) -> set[str]:
            with self.store.read() as conn:
                return self.store.source_ids(conn, kind)

        return ReadContext(now_ms=state.now, stored=stored, known_ids=known_ids)

    def _run_reader(self, reader: SourceReader, state: _RunState) -> None:
        try:
            batch = reader.read(self._context(state))
        except Exception:  # noqa: BLE001 — one broken store must not stop the others
            log.warning("brain map: reader %s failed", getattr(reader, "name", "?"), exc_info=True)
            state.stats.errors += 1
            return
        state.stats.errors += batch.errors
        for record in batch.records:
            state.stats.sources_seen += 1
            try:
                with self.store.write() as conn:
                    self._apply(conn, record, state)
            except sqlite3.Error:  # skipped and counted; the run goes on
                log.warning(
                    "brain map: could not index a %s record", record.source_kind, exc_info=True
                )
                state.stats.errors += 1
        for kind, ids in batch.present.items():
            self._tombstone(kind, ids, state)

    def _tombstone(self, kind: str, present: set[str], state: _RunState) -> None:
        """A row gone at its source takes everything derived from it along."""
        with self.store.write() as conn:
            gone = self.store.source_ids(conn, kind) - present
            for source_id in gone:
                for entity_id in self.store.entities_for_source(conn, kind, source_id):
                    state.dirty_projects |= projects_of(conn, entity_id)
                    self.store.delete_entity(conn, entity_id)
                    state.stats.entities_changed += 1
                for segment_id in self.store.segment_ids_for_source(conn, kind, source_id):
                    self.store.delete_segment(conn, segment_id)
                self.store.delete_source(conn, kind, source_id)
            state.dirty_projects -= {
                p for p in state.dirty_projects if self.store.get_entity(conn, p) is None
            }

    # ------------------------------------------------------------------
    # projection of one record
    # ------------------------------------------------------------------

    def _ensure_project(
        self, conn: sqlite3.Connection, key: str, title: str, locator: str, state: _RunState
    ) -> str:
        pid = project_id_for_key(key)
        if self.store.get_entity(conn, pid) is None:
            self.store.upsert_entity(
                conn,
                pid,
                ts_ms=state.now,
                kind="PROJECT",
                title=title or "Project",
                status="yellow",
                status_reason="no activity recorded yet",
                origin="derived",
                locator=locator,
            )
            self._add_name(state, pid, title)
            state.dirty_projects.add(pid)
        return pid

    def _usable_path(self, path: str) -> bool:
        if not path:
            return False
        normalized = os.path.normcase(path.rstrip("/\\"))
        return normalized not in ("", os.path.normcase(os.sep), self._home)

    def _resolve_project(self, conn: sqlite3.Connection, rec: Record, state: _RunState) -> str:
        if rec.project_path and self._usable_path(rec.project_path):
            title = rec.title if rec.defines_project else Path(rec.project_path).name
            return self._ensure_project(
                conn, project_key_for_path(rec.project_path), title, rec.project_path, state
            )
        if rec.project_name:
            wanted = slug(rec.project_name)
            same = [n for n in state.names if n.slug == wanted]
            path_projects = [
                n
                for n in same
                if (row := self.store.get_entity(conn, n.project_id)) is not None
                and row["source_kind"] != "wiki_project"
            ]
            if len(path_projects) == 1:
                return path_projects[0].project_id
            return self._ensure_project(conn, f"name:{wanted}", rec.title, rec.locator, state)
        return ""

    def _apply(self, conn: sqlite3.Connection, rec: Record, state: _RunState) -> None:
        project_id = self._resolve_project(conn, rec, state)
        defines = (
            rec.defines_project
            and project_id
            and (
                rec.source_kind != "wiki_project"
                or (self.store.get_entity(conn, project_id)["source_kind"] in ("", "wiki_project"))
            )
        )
        entity_kind = rec.entity_kind
        if rec.defines_project and not defines:
            entity_kind = "DOCUMENT"  # a wiki page about a project that exists already
        entity_id = project_id if defines else primary_id(rec.source_kind, rec.source_id)
        version_hash = f"{rec.version}|{project_id}"
        previous = self.store.get_source(conn, rec.source_kind, rec.source_id)
        if previous is None or previous["version_hash"] != version_hash:
            state.stats.sources_changed += 1

        fields: dict[str, object] = {
            "kind": entity_kind,
            "title": rec.title,
            "origin": "native",
            "native_status": rec.native_status,
            "source_kind": rec.source_kind,
            "source_id": rec.source_id,
            "locator": rec.locator,
            "user_priority": rec.user_priority,
            "due_ms": rec.due_ms,
        }
        if not defines:
            fields.update(
                status=rec.verdict.status,
                status_reason=rec.verdict.reason,
                confidence=rec.verdict.confidence,
                last_activity_ms=rec.last_activity_ms,
                review_at_ms=rec.verdict.review_at_ms,
            )
        # The same source row may have been projected under another id before
        # (a wiki page that defined a project and is now a page OF a project).
        for old_id in self.store.entities_for_source(conn, rec.source_kind, rec.source_id):
            old = self.store.get_entity(conn, old_id)
            if old_id != entity_id and old is not None and old["origin"] != "extracted":
                state.dirty_projects |= projects_of(conn, old_id)
                state.dirty_projects.discard(old_id)
                self.store.delete_entity(conn, old_id)
        changed, _ = self.store.upsert_entity(conn, entity_id, ts_ms=state.now, **fields)
        if changed:
            state.stats.entities_changed += 1
            state.dirty.add(entity_id)
        if defines:
            state.dirty_projects.add(entity_id)
            if rec.branch:
                state.branches[rec.branch] = entity_id

        # explicit edges
        if project_id and not defines:
            basis = "cwd" if rec.project_path else "wiki"
            if self.store.set_relation(
                conn,
                entity_id,
                "part_of",
                project_id,
                confidence=1.0 if rec.project_path else 0.9,
                assignment="confirmed",
                basis=basis,
                ts_ms=state.now,
            ):
                state.dirty.add(entity_id)
        for link in rec.links:
            target = primary_id(link.target_kind, link.target_id)
            if self.store.get_entity(conn, target) is None:
                continue
            src, dst = (target, entity_id) if link.reverse else (entity_id, target)
            if self.store.set_relation(
                conn,
                src,
                link.kind,
                dst,
                confidence=link.confidence,
                assignment="confirmed",
                basis="link",
                ts_ms=state.now,
            ):
                state.dirty.add(entity_id)
        if rec.agent_name:
            self._link_agent(conn, entity_id, rec, state)
        if not project_id and not rec.links and rec.terms_text:
            counts = self._name_hits(rec.terms_text, state)
            for pid, hits in counts.items():
                self._term_link(conn, entity_id, pid, hits, state, replace=True)

        if rec.messages:
            self._messages(conn, rec, entity_id, bool(project_id), state)
        if rec.full_messages and rec.messages is not None:
            wanted = {self._segment_id(rec, m) for m in rec.messages}
            for segment_id in self.store.segment_ids_for_source(
                conn, rec.source_kind, rec.source_id
            ):
                if segment_id not in wanted:
                    self._drop_segment(conn, segment_id, state)

        self.store.upsert_source(
            conn,
            rec.source_kind,
            rec.source_id,
            cursor=rec.cursor,
            version_hash=version_hash,
            cwd=rec.cwd,
            last_activity_ms=rec.last_activity_ms,
            indexed_ms=state.now,
        )

    def _link_agent(
        self, conn: sqlite3.Connection, entity_id: str, rec: Record, state: _RunState
    ) -> None:
        agent_id = f"agent:{_h(rec.agent_name.lower())}"
        current = self.store.get_entity(conn, agent_id)
        last = max(rec.last_activity_ms, int(current["last_activity_ms"]) if current else 0)
        active = state.now - last <= 7 * 24 * 3600 * 1000
        changed, _ = self.store.upsert_entity(
            conn,
            agent_id,
            ts_ms=state.now,
            kind="AGENT",
            title=rec.agent_name,
            status="blue" if active else "green",
            status_reason="active this week" if active else f"idle since {day(last)}",
            origin="derived",
            last_activity_ms=last,
        )
        if changed:
            state.dirty.add(agent_id)
        self.store.set_relation(
            conn,
            entity_id,
            "mentions",
            agent_id,
            confidence=1.0,
            assignment="confirmed",
            basis="agent",
            ts_ms=state.now,
        )

    # ------------------------------------------------------------------
    # clustering by names and branches
    # ------------------------------------------------------------------

    def _add_name(self, state: _RunState, project_id: str, title: str) -> None:
        name = slug(title)
        if len(name.replace("-", "")) < 4 or name in _GENERIC_NAMES:
            return
        if any(n.project_id == project_id for n in state.names):
            return
        words = name.split("-")
        # "personal-jarvis" matches "personal jarvis", "personal-jarvis",
        # "personal_jarvis" and "PersonalJarvis".
        joined = r"[\s_-]?".join(re.escape(w) for w in words)
        pattern = re.compile(rf"(?<![a-z0-9]){joined}(?![a-z0-9])", re.IGNORECASE)
        state.names.append(_ProjectName(project_id, name, pattern))

    def _load_names(self, conn: sqlite3.Connection, state: _RunState) -> None:
        for row in conn.execute(
            "SELECT entity_id, title FROM brain_map_entities WHERE kind='PROJECT'"
        ):
            self._add_name(state, str(row[0]), str(row[1]))

    def _name_hits(self, text: str, state: _RunState) -> dict[str, int]:
        hits: dict[str, int] = {}
        for name in state.names:
            found = len(name.pattern.findall(text))
            if found:
                hits[name.project_id] = hits.get(name.project_id, 0) + found
        for branch in rules.branch_names(text):
            pid = state.branches.get(branch)
            if pid:
                hits[pid] = hits.get(pid, 0) + 3  # a branch name is a strong signal
        return hits

    def _term_link(
        self,
        conn: sqlite3.Connection,
        entity_id: str,
        project_id: str,
        hits: int,
        state: _RunState,
        *,
        replace: bool = False,
    ) -> None:
        """Link by repeated distinctive names; below the floor only as a suggestion."""
        existing = conn.execute(
            "SELECT basis FROM brain_map_relations WHERE src_id=? AND kind='mentions' AND dst_id=?",
            (entity_id, project_id),
        ).fetchone()
        total = hits
        if existing is not None and not replace and str(existing[0]).startswith("terms:"):
            total += int(str(existing[0]).split(":", 1)[1] or 0)
        confidence = min(0.9, 0.3 + 0.15 * total)
        assignment = "confirmed" if confidence >= CONFIDENCE_FLOOR else "suggested"
        changed = self.store.set_relation(
            conn,
            entity_id,
            "mentions",
            project_id,
            confidence=confidence,
            assignment=assignment,
            basis=f"terms:{total}",
            ts_ms=state.now,
        )
        explicit = conn.execute(
            "SELECT 1 FROM brain_map_relations WHERE src_id=? AND kind='part_of'"
            " AND basis IN ('cwd', 'wiki', 'link')",
            (entity_id,),
        ).fetchone()
        if explicit is None:
            changed |= self.store.set_relation(
                conn,
                entity_id,
                "part_of",
                project_id,
                confidence=confidence,
                assignment=assignment,
                basis=f"terms:{total}",
                ts_ms=state.now,
            )
        if changed:
            state.dirty.add(entity_id)
            state.dirty_projects.add(project_id)

    # ------------------------------------------------------------------
    # segments and extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _segment_id(rec: Record, msg: Message) -> str:
        return (
            "seg:"
            + hashlib.sha256(
                f"{rec.source_kind}|{rec.source_id}|{msg.message_ref}".encode()
            ).hexdigest()[:24]
        )

    def _drop_segment(self, conn: sqlite3.Connection, segment_id: str, state: _RunState) -> None:
        for entity_id in self.store.delete_segment(conn, segment_id):
            self._forget_if_unsupported(conn, entity_id, state)

    def _forget_if_unsupported(
        self, conn: sqlite3.Connection, entity_id: str, state: _RunState
    ) -> None:
        row = self.store.get_entity(conn, entity_id)
        if row is None or row["origin"] != "extracted":
            return
        if self.store.evidence_for(conn, entity_id):
            state.dirty.add(entity_id)
            return
        state.dirty_projects |= projects_of(conn, entity_id)
        self.store.delete_entity(conn, entity_id)
        state.stats.entities_changed += 1

    def _messages(
        self,
        conn: sqlite3.Connection,
        rec: Record,
        conv_id: str,
        has_project: bool,
        state: _RunState,
    ) -> None:
        name_hits: dict[str, int] = {}
        for msg in rec.messages or []:
            state.stats.segments_seen += 1
            segment_id = self._segment_id(rec, msg)
            digest = content_hash(msg.text)
            existing = self.store.get_segment(conn, segment_id)
            if (
                existing is not None
                and existing["content_hash"] == digest
                and existing["extractor_version"] == RULES_VERSION
            ):
                continue
            state.stats.segments_processed += 1
            if existing is not None:
                self._drop_segment(conn, segment_id, state)
            signals = rules.extract(msg.text, role=msg.role)
            self._apply_signals(conn, rec, conv_id, segment_id, msg, signals, state)
            if not has_project:
                for pid, hits in self._name_hits(msg.text, state).items():
                    name_hits[pid] = name_hits.get(pid, 0) + hits
            self.store.upsert_segment(
                conn,
                segment_id=segment_id,
                source_kind=rec.source_kind,
                source_id=rec.source_id,
                conversation_id=conversation_id_of(rec.source_kind, rec.source_id),
                message_ref=msg.message_ref,
                message_id=msg.message_id,
                role=msg.role,
                ts_ms=msg.ts_ms,
                content_hash=digest,
                char_len=len(msg.text),
                extractor_version=RULES_VERSION,
                signal_count=len(signals),
                processed_ms=state.now,
            )
            if msg.role == "user" and len(msg.text) >= 40:
                state.model_segments.append((SegmentText(segment_id, msg.role, msg.text), rec, msg))
        for pid, hits in name_hits.items():
            self._term_link(conn, conv_id, pid, hits, state)

    def _apply_signals(
        self,
        conn: sqlite3.Connection,
        rec: Record,
        conv_id: str,
        segment_id: str,
        msg: Message,
        signals: list[rules.Signal],
        state: _RunState,
        *,
        extractor: str = RULES_VERSION,
    ) -> None:
        made: dict[str, list[tuple[str, rules.Signal]]] = {}
        for sig in signals:
            if sig.signal == "unblocked":
                self._resolve(conn, conv_id, "BLOCKER", sig, segment_id, rec, msg, state, extractor)
                continue
            if sig.kind == "MILESTONE":
                self._resolve(conn, conv_id, "TASK", sig, segment_id, rec, msg, state, extractor)
            entity_id = f"{sig.kind.lower()}:{_h(conv_id + '|' + _norm_title(sig.title))}"
            current = self.store.get_entity(conn, entity_id)
            last = max(msg.ts_ms, int(current["last_activity_ms"]) if current else 0)
            native = str(current["native_status"]) if current else ""
            resolved = native.split("|", 1)[1] if native.startswith("resolved|") else ""
            verdict = extracted_status(
                sig.kind,
                last_mention_ms=last,
                signal=sig.signal,
                resolved_reason=resolved,
                now_ms=state.now,
            )
            changed, _ = self.store.upsert_entity(
                conn,
                entity_id,
                ts_ms=state.now,
                kind=sig.kind,
                title=sig.title,
                status=verdict.status,
                status_reason=verdict.reason,
                confidence=max(sig.confidence, float(current["confidence"]) if current else 0.0),
                origin="extracted",
                native_status=native,
                source_kind=rec.source_kind,
                source_id=rec.source_id,
                signal=sig.signal,
                user_priority=max(sig.urgency, int(current["user_priority"]) if current else 0),
                last_activity_ms=last,
                review_at_ms=verdict.review_at_ms,
            )
            self.store.add_evidence(
                conn,
                entity_id=entity_id,
                segment_id=segment_id,
                source_kind=rec.source_kind,
                source_id=rec.source_id,
                conversation_id=conversation_id_of(rec.source_kind, rec.source_id),
                message_ref=msg.message_ref,
                message_id=msg.message_id,
                ts_ms=msg.ts_ms,
                quote=sig.quote,
                char_start=sig.char_start,
                char_end=sig.char_end,
                extractor=extractor,
                confidence=sig.confidence,
            )
            relation = {"DECISION": "decided_in", "BLOCKER": "blocks"}.get(sig.kind, "part_of")
            self.store.set_relation(
                conn,
                entity_id,
                relation,
                conv_id,
                confidence=1.0,
                assignment="confirmed",
                basis="said-in",
                ts_ms=state.now,
            )
            made.setdefault(sig.kind, []).append((entity_id, sig))
            state.dirty.add(entity_id)
            if changed:
                state.stats.entities_changed += 1
        # A blocker and a task in one message: the blocker blocks that task. A
        # task said after a decision in the same message follows from it.
        for blocker_id, _sig in made.get("BLOCKER", []):
            for task_id, _t in made.get("TASK", []):
                self.store.set_relation(
                    conn,
                    blocker_id,
                    "blocks",
                    task_id,
                    confidence=0.7,
                    assignment="confirmed",
                    basis="same-message",
                    ts_ms=state.now,
                )
        for decision_id, d_sig in made.get("DECISION", []):
            for task_id, t_sig in made.get("TASK", []):
                if t_sig.char_start > d_sig.char_start:
                    self.store.set_relation(
                        conn,
                        task_id,
                        "follows_from",
                        decision_id,
                        confidence=0.6,
                        assignment="confirmed",
                        basis="same-message",
                        ts_ms=state.now,
                    )

    def _resolve(
        self,
        conn: sqlite3.Connection,
        conv_id: str,
        kind: str,
        sig: rules.Signal,
        segment_id: str,
        rec: Record,
        msg: Message,
        state: _RunState,
        extractor: str,
    ) -> None:
        """Close open blockers ("unblocked") or tasks ("shipped X") of this chat.

        Only items said BEFORE in the same conversation, and only those that
        share words with the resolving sentence — or the single open one.
        """
        relation = "blocks" if kind == "BLOCKER" else "part_of"
        candidates = [
            row
            for row in conn.execute(
                "SELECT e.* FROM brain_map_relations r JOIN brain_map_entities e"
                " ON e.entity_id = r.src_id WHERE r.dst_id=? AND r.kind=? AND e.kind=?"
                " AND e.origin='extracted' AND e.native_status NOT LIKE 'resolved|%'"
                " AND e.last_activity_ms <= ?",
                (conv_id, relation, kind, msg.ts_ms),
            )
        ]
        words = _tokens(sig.quote)
        need = 1 if kind == "BLOCKER" else 2
        chosen = [r for r in candidates if len(_tokens(str(r["title"])) & words) >= need]
        if not chosen and kind == "BLOCKER" and len(candidates) == 1:
            chosen = candidates
        reason = f"resolved on {day(msg.ts_ms)}: “{sig.title[:80]}”"
        for row in chosen:
            verdict = Verdict("green", reason)
            self.store.upsert_entity(
                conn,
                str(row["entity_id"]),
                ts_ms=state.now,
                status=verdict.status,
                status_reason=verdict.reason,
                native_status=f"resolved|{reason}",
                review_at_ms=None,
            )
            self.store.add_evidence(
                conn,
                entity_id=str(row["entity_id"]),
                segment_id=segment_id,
                source_kind=rec.source_kind,
                source_id=rec.source_id,
                conversation_id=conversation_id_of(rec.source_kind, rec.source_id),
                message_ref=msg.message_ref,
                message_id=msg.message_id,
                ts_ms=msg.ts_ms,
                quote=sig.quote,
                char_start=sig.char_start,
                char_end=sig.char_end,
                extractor=f"{extractor}:resolution",
                confidence=sig.confidence,
            )
            state.dirty.add(str(row["entity_id"]))
            state.stats.entities_changed += 1

    # ------------------------------------------------------------------
    # optional model step (on demand only)
    # ------------------------------------------------------------------

    def _model_step(self, state: _RunState) -> None:
        if not self.extractor.available() or not state.model_segments:
            return
        pending = state.model_segments[:MODEL_SEGMENT_BUDGET]
        for start in range(0, len(pending), MODEL_BATCH):
            chunk = pending[start : start + MODEL_BATCH]
            texts = [c[0] for c in chunk]
            try:
                proposals = self.extractor.propose(texts)
            except Exception:  # noqa: BLE001 — the model tier is optional; rules already ran
                log.warning("brain map: extractor %s failed", self.extractor.name, exc_info=True)
                state.stats.errors += 1
                continue
            by_segment = {c[0].segment_id: c for c in chunk}
            for valid in validate(texts, proposals):
                seg, rec, msg = by_segment[valid.proposal.segment_id]
                quote = seg.text[valid.char_start : valid.char_end]
                signal = rules.Signal(
                    kind=valid.proposal.kind,
                    signal={"BLOCKER": "blocked", "DECISION": "decided", "TASK": "todo"}.get(
                        valid.proposal.kind, "shipped"
                    ),
                    title=" ".join(valid.proposal.title.split())[:120] or quote[:120],
                    quote=quote,
                    char_start=valid.char_start,
                    char_end=valid.char_end,
                    confidence=valid.confidence,
                    urgency=rules.urgency_of(quote),
                    language="",
                )
                with self.store.write() as conn:
                    self._apply_signals(
                        conn,
                        rec,
                        primary_id(rec.source_kind, rec.source_id),
                        seg.segment_id,
                        msg,
                        [signal],
                        state,
                        extractor=f"model:{self.extractor.name}",
                    )

    # ------------------------------------------------------------------
    # time-based re-derivation and project recompute
    # ------------------------------------------------------------------

    def _review_due(self, state: _RunState) -> None:
        with self.store.write() as conn:
            for row in self.store.due_for_review(conn, state.now):
                if row["origin"] != "extracted":
                    # Native rows were re-read and re-derived by their reader
                    # this run; a reader that did not run leaves them as they were.
                    if row["entity_id"] not in state.dirty and row["kind"] == "PROJECT":
                        state.dirty_projects.add(str(row["entity_id"]))
                    continue
                native = str(row["native_status"] or "")
                verdict = extracted_status(
                    str(row["kind"]),
                    last_mention_ms=int(row["last_activity_ms"]),
                    signal=str(row["signal"]),
                    resolved_reason=native.split("|", 1)[1]
                    if native.startswith("resolved|")
                    else "",
                    now_ms=state.now,
                )
                changed, _ = self.store.upsert_entity(
                    conn,
                    str(row["entity_id"]),
                    ts_ms=state.now,
                    status=verdict.status,
                    status_reason=verdict.reason,
                    review_at_ms=verdict.review_at_ms,
                )
                if changed:
                    state.dirty.add(str(row["entity_id"]))
                    state.stats.entities_changed += 1

    def _recompute(self, state: _RunState) -> None:
        with self.store.write() as conn:
            for entity_id in state.dirty:
                state.dirty_projects |= projects_of(conn, entity_id)
                row = self.store.get_entity(conn, entity_id)
                if row is not None and row["kind"] != "PROJECT":
                    blockers = open_blockers(conn, entity_id)
                    score, reason = priority(row, now=state.now, blockers=blockers)
                    self.store.set_priority(conn, entity_id, score, reason)
        for project_id in sorted(state.dirty_projects):
            with self.store.write() as conn:
                if self._recompute_project(conn, project_id, state):
                    state.stats.projects_recomputed += 1

    def _recompute_project(
        self, conn: sqlite3.Connection, project_id: str, state: _RunState
    ) -> bool:
        project = self.store.get_entity(conn, project_id)
        if project is None:
            return False
        members = members_of(conn, project_id)
        own = own_verdict(conn, project, state.now)
        src = conn.execute(
            "SELECT last_activity_ms FROM brain_map_sources WHERE source_kind=? AND source_id=?",
            (project["source_kind"], project["source_id"]),
        ).fetchone()
        own_last = int(src[0]) if src is not None else 0
        for member in members:
            if member.row["entity_id"] in state.dirty:
                continue
            blockers = open_blockers(conn, str(member.row["entity_id"]))
            score, reason = priority(member.row, now=state.now, blockers=blockers)
            self.store.set_priority(conn, str(member.row["entity_id"]), score, reason)
        members = members_of(conn, project_id)
        result = rollup(project, own, members, own_last)
        blocker_rows = [
            m.row
            for m in members
            if m.row["kind"] == "BLOCKER"
            and m.row["status"] in ("red", "yellow")
            and m.assignment == "confirmed"
        ]
        self.store.upsert_entity(
            conn,
            project_id,
            ts_ms=state.now,
            status=result.verdict.status,
            status_reason=result.verdict.reason,
            confidence=result.verdict.confidence,
            last_activity_ms=result.last_activity_ms,
            summary=result.summary,
            input_hash=result.input_hash,
            # Members carry their own review instants; only the project's own
            # record (IDE / wiki page going stale) needs one here.
            review_at_ms=own.review_at_ms if own is not None else None,
        )
        refreshed = self.store.get_entity(conn, project_id)
        score, reason = priority(
            refreshed,
            now=state.now,
            blockers=blocker_rows,
            open_blocker_count=result.open_blockers,
        )
        self.store.set_priority(conn, project_id, score, reason)
        return True


__all__ = [
    "BrainMapIndexer",
    "RunStats",
    "content_hash",
    "primary_id",
    "project_id_for_key",
    "project_key_for_path",
    "slug",
]
