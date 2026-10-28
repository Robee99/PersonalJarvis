from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from jarvis.core.config import CogneeMemoryConfig
from jarvis.memory.cognee_adapter import CogneeMemoryAdapter
from jarvis.plugins.tool.cognee_source import CogneeSourceTool


class FakeCogneeAdapter:
    enabled = True

    def __init__(self) -> None:
        self.indexed: list[Path] = []
        self.forgotten: list[Path] = []

    async def index_path(self, path: Path) -> dict[str, object]:
        self.indexed.append(path)
        return {"source_id": "stable-test-id", "unchanged": False}

    async def forget_path(self, path: Path) -> dict[str, object]:
        self.forgotten.append(path)
        return {"source_id": "stable-test-id"}


@pytest.mark.asyncio
async def test_source_tool_indexes_only_vault_markdown(tmp_path: Path) -> None:
    vault = tmp_path / "wiki"
    vault.mkdir()
    note = vault / "people" / "joy.md"
    note.parent.mkdir()
    note.write_text("# Joy\nJoy likes astronomy.", encoding="utf-8")
    adapter = FakeCogneeAdapter()
    tool = CogneeSourceTool(adapter, vault)

    result = await tool.execute({"action": "index", "path": "people/joy.md"}, None)

    assert result.success
    assert adapter.indexed == [note.resolve()]


@pytest.mark.asyncio
async def test_source_tool_rejects_traversal_without_calling_adapter(tmp_path: Path) -> None:
    vault = tmp_path / "wiki"
    vault.mkdir()
    adapter = FakeCogneeAdapter()
    tool = CogneeSourceTool(adapter, vault)

    result = await tool.execute({"action": "index", "path": "../private.md"}, None)

    assert not result.success
    assert "wiki-relative" in (result.error or "")
    assert adapter.indexed == []


def test_cognee_is_opt_in_and_isolated_by_default() -> None:
    config = CogneeMemoryConfig()
    assert config.enabled is False
    assert config.python_executable == ""


def test_semantic_source_map_excludes_stale_and_deleted_records(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    data = tmp_path / "cognee"
    vault.mkdir()
    data.mkdir()
    current = "# Current\nA source-linked fact."
    note = vault / "entities" / "current.md"
    note.parent.mkdir()
    note.write_text(current, encoding="utf-8")
    stale = vault / "entities" / "stale.md"
    stale.write_text("# Updated\nThe indexed copy is obsolete.", encoding="utf-8")
    state = data / "source-state.sqlite3"
    with sqlite3.connect(state) as conn:
        conn.execute(
            "CREATE TABLE sources (source_id TEXT, source_path TEXT, "
            "content_hash TEXT, status TEXT)"
        )
        conn.executemany(
            "INSERT INTO sources VALUES (?, ?, ?, 'active')",
            [
                (
                    "source-current",
                    "entities/current.md",
                    hashlib.sha256(current.encode()).hexdigest(),
                ),
                (
                    "source-stale",
                    "entities/stale.md",
                    hashlib.sha256(b"# Old").hexdigest(),
                ),
                (
                    "source-deleted",
                    "entities/deleted.md",
                    hashlib.sha256(b"# Deleted").hexdigest(),
                ),
            ],
        )

    adapter = CogneeMemoryAdapter(
        vault_root=vault,
        config=CogneeMemoryConfig(enabled=True, data_dir=str(data)),
    )

    assert adapter.list_active_sources() == [
        {"source_id": "source-current", "source_path": "entities/current.md"}
    ]
