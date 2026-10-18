"""Real Cognee acceptance test for explicitly selected conversation sources."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from jarvis.core.config import CogneeMemoryConfig
from jarvis.memory.cognee_adapter import CogneeMemoryAdapter


@pytest.mark.asyncio
async def test_selected_conversation_persists_updates_and_forgets(tmp_path: Path) -> None:
    python = os.environ.get("JARVIS_COGNEE_PYTHON")
    if not python:
        pytest.skip("set JARVIS_COGNEE_PYTHON to the isolated worker Python")

    root = await asyncio.to_thread(
        Path(os.environ.get("JARVIS_COGNEE_CONVERSATION_TEST_ROOT", str(tmp_path))).resolve
    )
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    vault = root / "vault"
    await asyncio.to_thread(vault.mkdir, exist_ok=True)
    config = CogneeMemoryConfig(
        enabled=True,
        python_executable=python,
        worker_script=str(
            Path(__file__).parents[2] / "jarvis" / "memory" / "cognee_worker.py"
        ),
        data_dir=str(root / "data"),
        dataset_name="jarvis-cognee-conversation-acceptance",
        timeout_s=240,
    )
    thread_id = "acceptance-thread-with-private-provenance"
    initial_messages = [
        {"role": "user", "text": "Where is the green comet observatory?", "timestamp_ns": 1},
        {"role": "assistant", "text": "It is on Mars near a violet telescope.", "timestamp_ns": 2},
        {"role": "tool", "text": "secret tool payload must never be indexed", "timestamp_ns": 3},
    ]

    first = CogneeMemoryAdapter(vault_root=vault, config=config)
    indexed = await first.index_conversation(thread_id, initial_messages)
    assert indexed["indexed"] is True
    source_id = indexed["source_id"]

    restarted = CogneeMemoryAdapter(vault_root=vault, config=config)
    original = await restarted.search_conversations("green comet observatory violet telescope", k=5)
    assert original
    assert original[0]["source_id"] == source_id
    assert original[0]["source_ref"] == thread_id
    assert all("secret tool payload" not in item["text"] for item in original)

    updated_messages = [
        {"role": "user", "text": "Where is the blue satellite laboratory?", "timestamp_ns": 4},
        {
            "role": "assistant",
            "text": "It is on Europa with a silver radio dish.",
            "timestamp_ns": 5,
        },
    ]
    await restarted.index_conversation(thread_id, updated_messages)
    updated = await restarted.search_conversations("Europa silver radio dish", k=5)
    assert updated and updated[0]["source_id"] == source_id
    stale = await restarted.search_conversations("Mars violet telescope", k=5)
    assert not any(
        "Mars" in item["text"] or "violet telescope" in item["text"] for item in stale
    )

    await restarted.forget_conversation(thread_id)
    forgotten = await CogneeMemoryAdapter(
        vault_root=vault, config=config
    ).search_conversations("Europa silver radio dish", k=5)
    assert not any(item["source_id"] == source_id for item in forgotten)
