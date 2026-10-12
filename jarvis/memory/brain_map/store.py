"""SQLite access for the brain map tables in ``data/jarvis.db``.

Schema source of truth: ``jarvis/memory/migrations/0010_brain_map.sql``,
applied by ``run_migrations`` for RecallStore-opened databases and executed
idempotently here for a standalone open — the ``CandidateJournal`` pattern
(``jarvis/memory/wiki/journal.py``): ``user_version`` is never bumped here, so
a standalone open can never make an earlier migration look applied.

Synchronous ``sqlite3`` behind a ``threading.Lock``. Every caller is a
background thread or a threadpool route handler, never the voice path (AP-9).
Reads use their own connection so a running index pass never stalls the map
(the database is in WAL mode).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from jarvis.core.redact import redact_secrets

from .constants import MAX_QUOTE_CHARS

_MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0010_brain_map.sql"

#: Entity columns a caller may set through :meth:`BrainMapStore.upsert_entity`.
ENTITY_FIELDS: tuple[str, ...] = (
    "kind",
    "title",
    "status",
    "status_reason",
    "confidence",
    "origin",
    "native_status",
    "source_kind",
    "source_id",
    "locator",
    "signal",
    "user_priority",
    "due_ms",
    "first_seen_ms",
    "last_activity_ms",
    "review_at_ms",
    "priority_score",
    "priority_reason",
    "summary",
    "input_hash",
)


def now_ms() -> int:
    return int(time.time() * 1000)


class BrainMapStore:
    """CRUD over the ``brain_map_*`` tables."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._read_lock = threading.Lock()
        self._read_conn: sqlite3.Connection | None = None
        self._closed = False

    # ------------------------------------------------------------------
    # connections
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _writer(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("BrainMapStore is closed")
        if self._conn is None:
            conn = self._connect()
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_MIGRATION.read_text(encoding="utf-8"))
            self._conn = conn
        return self._conn

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """One transaction on the write connection; rolled back on error."""
        with self._lock:
            conn = self._writer()
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """The read connection (schema guaranteed by a writer open first)."""
        with self._lock:
            self._writer()
        with self._read_lock:
            if self._closed:
                raise RuntimeError("BrainMapStore is closed")
            if self._read_conn is None:
                self._read_conn = self._connect()
            yield self._read_conn

    def close(self) -> None:
        with self._lock, self._read_lock:
            self._closed = True
            for conn in (self._conn, self._read_conn):
                if conn is not None:
                    conn.close()
            self._conn = None
            self._read_conn = None

    # ------------------------------------------------------------------
    # sources
    # ------------------------------------------------------------------

    @staticmethod
    def get_source(conn: sqlite3.Connection, kind: str, source_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM brain_map_sources WHERE source_kind=? AND source_id=?",
            (kind, source_id),
        ).fetchone()

    @staticmethod
    def upsert_source(
        conn: sqlite3.Connection,
        kind: str,
        source_id: str,
        *,
        cursor: str,
        version_hash: str,
        cwd: str,
        last_activity_ms: int,
        indexed_ms: int,
    ) -> None:
        conn.execute(
            "INSERT INTO brain_map_sources (source_kind, source_id, cursor, version_hash, cwd,"
            " last_activity_ms, indexed_ms) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(source_kind, source_id) DO UPDATE SET cursor=excluded.cursor,"
            " version_hash=excluded.version_hash, cwd=excluded.cwd,"
            " last_activity_ms=excluded.last_activity_ms, indexed_ms=excluded.indexed_ms",
            (kind, source_id, cursor, version_hash, cwd, last_activity_ms, indexed_ms),
        )

    @staticmethod
    def source_ids(conn: sqlite3.Connection, kind: str) -> set[str]:
        return {
            str(r[0])
            for r in conn.execute(
                "SELECT source_id FROM brain_map_sources WHERE source_kind=?", (kind,)
            )
        }

    @staticmethod
    def delete_source(conn: sqlite3.Connection, kind: str, source_id: str) -> None:
        conn.execute(
            "DELETE FROM brain_map_sources WHERE source_kind=? AND source_id=?", (kind, source_id)
        )

    # ------------------------------------------------------------------
    # segments
    # ------------------------------------------------------------------

    @staticmethod
    def get_segment(conn: sqlite3.Connection, segment_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM brain_map_segments WHERE segment_id=?", (segment_id,)
        ).fetchone()

    @staticmethod
    def upsert_segment(conn: sqlite3.Connection, **row: Any) -> None:
        cols = (
            "segment_id",
            "source_kind",
            "source_id",
            "conversation_id",
            "message_ref",
            "message_id",
            "role",
            "ts_ms",
            "content_hash",
            "char_len",
            "extractor_version",
            "signal_count",
            "processed_ms",
        )
        values = [row[c] for c in cols]
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols[1:])
        conn.execute(
            f"INSERT INTO brain_map_segments ({', '.join(cols)}) VALUES"  # noqa: S608 — only '?' placeholders and fixed column names are interpolated
            f" ({', '.join('?' for _ in cols)}) ON CONFLICT(segment_id) DO UPDATE SET {updates}",
            values,
        )

    @staticmethod
    def segment_ids_for_source(conn: sqlite3.Connection, kind: str, source_id: str) -> list[str]:
        return [
            str(r[0])
            for r in conn.execute(
                "SELECT segment_id FROM brain_map_segments WHERE source_kind=? AND source_id=?",
                (kind, source_id),
            )
        ]

    @staticmethod
    def delete_segment(conn: sqlite3.Connection, segment_id: str) -> set[str]:
        """Drop a segment and its evidence; return the entities that lost evidence."""
        touched = {
            str(r[0])
            for r in conn.execute(
                "SELECT DISTINCT entity_id FROM brain_map_evidence WHERE segment_id=?",
                (segment_id,),
            )
        }
        conn.execute("DELETE FROM brain_map_evidence WHERE segment_id=?", (segment_id,))
        conn.execute("DELETE FROM brain_map_segments WHERE segment_id=?", (segment_id,))
        return touched

    # ------------------------------------------------------------------
    # entities
    # ------------------------------------------------------------------

    @staticmethod
    def get_entity(conn: sqlite3.Connection, entity_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM brain_map_entities WHERE entity_id=?", (entity_id,)
        ).fetchone()

    @staticmethod
    def upsert_entity(
        conn: sqlite3.Connection, entity_id: str, *, ts_ms: int, **fields: Any
    ) -> tuple[bool, bool]:
        """Insert or update; return ``(changed, status_changed)``.

        A status change (or the first status) is appended to the history.
        ``first_seen_ms`` is kept from the first write.
        """
        unknown = set(fields) - set(ENTITY_FIELDS)
        if unknown:
            raise ValueError(f"unknown entity fields: {sorted(unknown)}")
        current = BrainMapStore.get_entity(conn, entity_id)
        if current is None:
            data = {"first_seen_ms": ts_ms, **fields}
            cols = ["entity_id", *data, "last_updated_ms"]
            conn.execute(
                f"INSERT INTO brain_map_entities ({', '.join(cols)}) VALUES"  # noqa: S608 — only '?' placeholders and fixed column names are interpolated
                f" ({', '.join('?' for _ in cols)})",
                [entity_id, *data.values(), ts_ms],
            )
            status = str(fields.get("status") or "blue")
            BrainMapStore._history(
                conn, entity_id, ts_ms, status, str(fields.get("status_reason", ""))
            )
            return True, True
        fields.pop("first_seen_ms", None)
        diff = {k: v for k, v in fields.items() if current[k] != v}
        if not diff:
            return False, False
        assignments = ", ".join(f"{k}=?" for k in diff)
        conn.execute(
            f"UPDATE brain_map_entities SET {assignments}, last_updated_ms=? WHERE entity_id=?",  # noqa: S608 — only '?' placeholders and fixed column names are interpolated
            [*diff.values(), ts_ms, entity_id],
        )
        status_changed = "status" in diff
        if status_changed:
            BrainMapStore._history(
                conn,
                entity_id,
                ts_ms,
                str(fields["status"]),
                str(fields.get("status_reason", current["status_reason"])),
            )
        return True, status_changed

    @staticmethod
    def _history(
        conn: sqlite3.Connection, entity_id: str, ts_ms: int, status: str, reason: str
    ) -> None:
        conn.execute(
            "INSERT INTO brain_map_status_history (entity_id, ts_ms, status, reason)"
            " VALUES (?, ?, ?, ?)",
            (entity_id, ts_ms, status, reason),
        )

    @staticmethod
    def set_priority(conn: sqlite3.Connection, entity_id: str, score: float, reason: str) -> None:
        conn.execute(
            "UPDATE brain_map_entities SET priority_score=?, priority_reason=?"
            " WHERE entity_id=? AND (priority_score<>? OR priority_reason<>?)",
            (score, reason, entity_id, score, reason),
        )

    @staticmethod
    def delete_entity(conn: sqlite3.Connection, entity_id: str) -> None:
        conn.execute("DELETE FROM brain_map_evidence WHERE entity_id=?", (entity_id,))
        conn.execute("DELETE FROM brain_map_status_history WHERE entity_id=?", (entity_id,))
        conn.execute(
            "DELETE FROM brain_map_relations WHERE src_id=? OR dst_id=?", (entity_id, entity_id)
        )
        conn.execute("DELETE FROM brain_map_entities WHERE entity_id=?", (entity_id,))

    @staticmethod
    def entities_for_source(conn: sqlite3.Connection, kind: str, source_id: str) -> list[str]:
        return [
            str(r[0])
            for r in conn.execute(
                "SELECT entity_id FROM brain_map_entities WHERE source_kind=? AND source_id=?",
                (kind, source_id),
            )
        ]

    @staticmethod
    def due_for_review(conn: sqlite3.Connection, now: int) -> list[sqlite3.Row]:
        return list(
            conn.execute(
                "SELECT * FROM brain_map_entities WHERE review_at_ms IS NOT NULL"
                " AND review_at_ms <= ?",
                (now,),
            )
        )

    # ------------------------------------------------------------------
    # relations
    # ------------------------------------------------------------------

    @staticmethod
    def set_relation(
        conn: sqlite3.Connection,
        src: str,
        kind: str,
        dst: str,
        *,
        confidence: float,
        assignment: str,
        basis: str,
        ts_ms: int,
    ) -> bool:
        """Upsert one edge; return whether anything changed."""
        row = conn.execute(
            "SELECT confidence, assignment, basis FROM brain_map_relations"
            " WHERE src_id=? AND kind=? AND dst_id=?",
            (src, kind, dst),
        ).fetchone()
        confidence = round(min(1.0, max(0.0, confidence)), 3)
        if row is not None and (row[0], row[1], row[2]) == (confidence, assignment, basis):
            return False
        conn.execute(
            "INSERT INTO brain_map_relations (src_id, kind, dst_id, confidence, assignment,"
            " basis, updated_ms) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(src_id, kind, dst_id) DO UPDATE SET confidence=excluded.confidence,"
            " assignment=excluded.assignment, basis=excluded.basis,"
            " updated_ms=excluded.updated_ms",
            (src, kind, dst, confidence, assignment, basis, ts_ms),
        )
        return True

    @staticmethod
    def relations_from(
        conn: sqlite3.Connection, src: str, kinds: Iterable[str] | None = None
    ) -> list[sqlite3.Row]:
        rows = conn.execute("SELECT * FROM brain_map_relations WHERE src_id=?", (src,)).fetchall()
        wanted = set(kinds) if kinds is not None else None
        return [r for r in rows if wanted is None or r["kind"] in wanted]

    @staticmethod
    def relations_to(
        conn: sqlite3.Connection, dst: str, kinds: Iterable[str] | None = None
    ) -> list[sqlite3.Row]:
        rows = conn.execute("SELECT * FROM brain_map_relations WHERE dst_id=?", (dst,)).fetchall()
        wanted = set(kinds) if kinds is not None else None
        return [r for r in rows if wanted is None or r["kind"] in wanted]

    @staticmethod
    def delete_relations_from(conn: sqlite3.Connection, src: str, kind: str) -> None:
        conn.execute("DELETE FROM brain_map_relations WHERE src_id=? AND kind=?", (src, kind))

    # ------------------------------------------------------------------
    # evidence
    # ------------------------------------------------------------------

    @staticmethod
    def add_evidence(
        conn: sqlite3.Connection,
        *,
        entity_id: str,
        segment_id: str,
        source_kind: str,
        source_id: str,
        conversation_id: str,
        message_ref: str,
        message_id: str,
        ts_ms: int,
        quote: str,
        char_start: int,
        char_end: int,
        extractor: str,
        confidence: float,
    ) -> None:
        # The quote is the person's own words; a secret-shaped token is masked
        # all the same, and the length is bounded (audit §9 "Privacy").
        stored = redact_secrets(quote)[:MAX_QUOTE_CHARS]
        conn.execute(
            "INSERT OR REPLACE INTO brain_map_evidence (entity_id, segment_id, source_kind,"
            " source_id, conversation_id, message_ref, message_id, ts_ms, quote, char_start,"
            " char_end, extractor, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entity_id,
                segment_id,
                source_kind,
                source_id,
                conversation_id,
                message_ref,
                message_id,
                ts_ms,
                stored,
                char_start,
                char_end,
                extractor,
                round(min(1.0, max(0.0, confidence)), 3),
            ),
        )

    @staticmethod
    def evidence_for(conn: sqlite3.Connection, entity_id: str) -> list[sqlite3.Row]:
        return list(
            conn.execute(
                "SELECT * FROM brain_map_evidence WHERE entity_id=? ORDER BY ts_ms, evidence_id",
                (entity_id,),
            )
        )

    # ------------------------------------------------------------------
    # runs
    # ------------------------------------------------------------------

    @staticmethod
    def start_run(conn: sqlite3.Connection, trigger: str, started: int) -> int:
        cur = conn.execute(
            "INSERT INTO brain_map_runs (trigger, started_ms) VALUES (?, ?)", (trigger, started)
        )
        return int(cur.lastrowid or 0)

    @staticmethod
    def finish_run(conn: sqlite3.Connection, run_id: int, **stats: int) -> None:
        cols = ", ".join(f"{k}=?" for k in stats)
        conn.execute(f"UPDATE brain_map_runs SET {cols} WHERE run_id=?", [*stats.values(), run_id])  # noqa: S608 — only '?' placeholders and fixed column names are interpolated


__all__ = ["ENTITY_FIELDS", "BrainMapStore", "now_ms"]
