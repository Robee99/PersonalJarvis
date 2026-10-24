"""Conversation IDs and source scopes at the production worker boundary."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.core.config import CogneeMemoryConfig
from jarvis.memory.cognee_adapter import CogneeMemoryAdapter


@pytest.mark.asyncio
async def test_similar_thread_names_cannot_overwrite_each_others_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = CogneeMemoryAdapter(
        vault_root=tmp_path / "vault",
        config=CogneeMemoryConfig(enabled=True, data_dir=str(tmp_path / "data")),
    )
    requests: list[dict[str, Any]] = []

    def worker(payload: dict[str, Any]) -> dict[str, Any]:
        requests.append(payload)
        return {"ok": True, "source_id": payload["source_id"]}

    monkeypatch.setattr(adapter, "_request", worker)
    messages = [
        {"role": "user", "text": "A synthetic conversation fact."},
        {"role": "tool", "text": "Tool-only material must stay out."},
    ]
    first = await adapter.index_conversation("project/thread", messages)
    second = await adapter.index_conversation("project?thread", messages)
    updated = await adapter.index_conversation(
        "project/thread", [{"role": "assistant", "text": "An updated synthetic fact."}]
    )
    await adapter.forget_conversation("project/thread")

    assert first["source_id"] != second["source_id"]
    assert updated["source_id"] == first["source_id"]
    assert requests[0]["source_path"] != requests[1]["source_path"]
    assert requests[0]["source_ref"] == "project/thread"
    assert requests[1]["source_ref"] == "project?thread"
    assert "Tool-only material" not in requests[0]["content"]
    assert requests[3]["op"] == "forget"
    assert requests[3]["source_id"] == first["source_id"]
    assert requests[3]["source_id"] != second["source_id"]


@pytest.mark.asyncio
async def test_conversation_and_wiki_search_keep_distinct_worker_scopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = CogneeMemoryAdapter(
        vault_root=tmp_path / "vault",
        config=CogneeMemoryConfig(enabled=True, data_dir=str(tmp_path / "data")),
    )
    requests: list[dict[str, Any]] = []

    def worker(payload: dict[str, Any]) -> dict[str, Any]:
        requests.append(payload)
        return {"ok": True, "results": []}

    monkeypatch.setattr(adapter, "_request", worker)
    await adapter.search_conversations("synthetic fact")
    await adapter.search("synthetic fact")

    assert requests[0]["source_types"] == ["conversation"]
    assert requests[1]["source_types"] == ["wiki"]
