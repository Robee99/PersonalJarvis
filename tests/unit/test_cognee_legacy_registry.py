"""Legacy semantic registries must retain source visibility and privacy checks."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jarvis.core.config import CogneeMemoryConfig
from jarvis.memory.cognee_adapter import CogneeMemoryAdapter, CogneeUnavailable


@pytest.mark.parametrize("typed_sources", [False, True])
def test_private_wiki_sources_are_denied_before_reconciliation(
    tmp_path: Path, typed_sources: bool
) -> None:
    vault = tmp_path / "vault"
    data = tmp_path / "data"
    vault.mkdir()
    data.mkdir()
    (vault / "private.md").write_text("# Private\npassword: synthetic-test-value")
    with sqlite3.connect(data / "source-state.sqlite3") as conn:
        typed_column = ", source_type TEXT DEFAULT 'wiki'" if typed_sources else ""
        conn.execute(
            "CREATE TABLE sources (source_id TEXT, source_path TEXT, "
            "content_hash TEXT, status TEXT" + typed_column + ")"
        )
        conn.execute(
            "INSERT INTO sources(source_id, source_path, content_hash, status) "
            "VALUES ('private-source', 'private.md', 'old-hash', 'active')"
        )
        if typed_sources:
            conn.execute(
                "INSERT INTO sources VALUES "
                "('conversation', 'conversations/thread.md', 'hash', 'active', 'conversation')"
            )
    adapter = CogneeMemoryAdapter(
        vault_root=vault,
        config=CogneeMemoryConfig(enabled=True, data_dir=str(data)),
    )

    assert adapter._sources_with_unsafe_content(data) == ["private-source"]


@pytest.mark.parametrize("operation", ["list", "deny", "conversations"])
def test_broken_source_schema_is_reported_instead_of_hidden(
    tmp_path: Path, operation: str
) -> None:
    vault = tmp_path / "vault"
    data = tmp_path / "data"
    vault.mkdir()
    data.mkdir()
    with sqlite3.connect(data / "source-state.sqlite3") as conn:
        conn.execute("CREATE TABLE sources (source_id TEXT, source_path TEXT)")
    adapter = CogneeMemoryAdapter(
        vault_root=vault,
        config=CogneeMemoryConfig(enabled=True, data_dir=str(data)),
    )

    with pytest.raises(CogneeUnavailable, match="could not be checked safely"):
        if operation == "list":
            adapter.list_active_sources()
        elif operation == "deny":
            adapter._sources_with_unsafe_content(data)
        else:
            adapter.list_conversation_sources()


def test_legacy_wiki_registry_has_no_conversation_sources(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    data = tmp_path / "data"
    vault.mkdir()
    data.mkdir()
    with sqlite3.connect(data / "source-state.sqlite3") as conn:
        conn.execute(
            "CREATE TABLE sources (source_id TEXT, source_path TEXT, "
            "content_hash TEXT, status TEXT)"
        )
    adapter = CogneeMemoryAdapter(
        vault_root=vault,
        config=CogneeMemoryConfig(enabled=True, data_dir=str(data)),
    )
    assert adapter.list_conversation_sources() == []


@pytest.mark.parametrize(
    "columns",
    [
        "source_id TEXT, source_path TEXT, content_hash TEXT, source_type TEXT, source_ref TEXT",
        "source_id TEXT, source_path TEXT, status TEXT, source_type TEXT, source_ref TEXT",
        "source_id TEXT, source_path TEXT, content_hash TEXT, status TEXT, source_type TEXT",
        "source_id TEXT, source_path TEXT, content_hash TEXT, status TEXT, source_ref TEXT",
    ],
)
def test_partial_conversation_schema_is_not_treated_as_legacy(
    tmp_path: Path, columns: str
) -> None:
    vault = tmp_path / "vault"
    data = tmp_path / "data"
    vault.mkdir()
    data.mkdir()
    with sqlite3.connect(data / "source-state.sqlite3") as conn:
        conn.execute("CREATE TABLE sources (" + columns + ")")
    adapter = CogneeMemoryAdapter(
        vault_root=vault,
        config=CogneeMemoryConfig(enabled=True, data_dir=str(data)),
    )
    with pytest.raises(CogneeUnavailable, match="could not be checked safely"):
        adapter.list_conversation_sources()
