"""Migration 0010 applies cleanly, and its vocabulary matches every layer (AP-4)."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import get_args

from jarvis.memory.brain_map import constants
from jarvis.memory.brain_map.store import BrainMapStore
from jarvis.memory.migration_runner import MIGRATIONS_DIR, run_migrations_sync
from jarvis.ui.web import brain_map_routes

_SQL = (MIGRATIONS_DIR / "0010_brain_map.sql").read_text(encoding="utf-8")
_SCHEMA = Path(__file__).resolve().parents[4] / "jarvis" / "memory" / "schema.sql"
_TABLES = {
    "brain_map_sources",
    "brain_map_segments",
    "brain_map_entities",
    "brain_map_relations",
    "brain_map_evidence",
    "brain_map_status_history",
    "brain_map_runs",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_migrations_apply_on_a_fresh_recall_database(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "jarvis.db")
    try:
        conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
        head = run_migrations_sync(conn)
        assert head >= 10
        assert _TABLES <= _tables(conn)
        assert run_migrations_sync(conn) == head  # idempotent
    finally:
        conn.close()


def test_migration_0010_applies_on_a_database_at_0009(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "legacy.db")
    try:
        conn.execute("PRAGMA user_version = 9")
        assert run_migrations_sync(conn) >= 10
        assert _TABLES <= _tables(conn)
    finally:
        conn.close()


def test_standalone_store_open_does_not_bump_user_version(tmp_path: Path) -> None:
    db = tmp_path / "jarvis.db"
    store = BrainMapStore(db)
    with store.write() as conn:
        conn.execute("SELECT 1")
    store.close()
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
        head = run_migrations_sync(conn)  # re-running 0010 over the tables is a no-op
        assert head >= 10
        assert _TABLES <= _tables(conn)
    finally:
        conn.close()


def _check_list(table: str, column: str) -> set[str]:
    body = _SQL.split(f"CREATE TABLE IF NOT EXISTS {table}", 1)[1].split(");", 1)[0]
    match = re.search(rf"\b{column}\b[^,]*?CHECK \({column} IN \(([^)]*)\)\)", body, re.S)
    assert match, (table, column)
    return set(re.findall(r"'([^']+)'", match.group(1)))


def test_entity_kinds_match_sql_and_pydantic() -> None:
    expected = set(constants.ENTITY_KINDS)
    assert set(get_args(constants.EntityKind)) == expected
    assert _check_list("brain_map_entities", "kind") == expected
    assert set(get_args(brain_map_routes.EntityKindLiteral)) == expected


def test_statuses_match_sql_and_pydantic() -> None:
    expected = set(constants.STATUSES)
    assert set(get_args(constants.Status)) == expected
    assert _check_list("brain_map_entities", "status") == expected
    assert _check_list("brain_map_status_history", "status") == expected
    assert set(get_args(brain_map_routes.StatusLiteral)) == expected


def test_relation_kinds_assignments_origins_and_sources_match() -> None:
    assert _check_list("brain_map_relations", "kind") == set(constants.RELATION_KINDS)
    assert set(get_args(brain_map_routes.RelationKindLiteral)) == set(constants.RELATION_KINDS)
    assert _check_list("brain_map_relations", "assignment") == set(constants.ASSIGNMENTS)
    assert set(get_args(brain_map_routes.AssignmentLiteral)) == set(constants.ASSIGNMENTS)
    assert _check_list("brain_map_entities", "origin") == set(constants.ORIGINS)
    assert set(get_args(brain_map_routes.OriginLiteral)) == set(constants.ORIGINS)
    assert _check_list("brain_map_sources", "source_kind") == set(constants.SOURCE_KINDS)
    assert set(get_args(constants.SourceKind)) == set(constants.SOURCE_KINDS)
