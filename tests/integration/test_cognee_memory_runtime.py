"""Real isolated Cognee acceptance test (set JARVIS_COGNEE_PYTHON to run)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from jarvis.core.config import CogneeMemoryConfig
from jarvis.memory.cognee_adapter import CogneeMemoryAdapter


@pytest.mark.asyncio
async def test_real_cognee_memory_persists_updates_and_forgets(tmp_path: Path) -> None:
    python = os.environ.get("JARVIS_COGNEE_PYTHON")
    if not python:
        pytest.skip("set JARVIS_COGNEE_PYTHON to the isolated Cognee Python for runtime acceptance")
    # Windows Ladybug/Lance append several nested components; the opt-in
    # runtime test accepts a short scratch root to avoid MAX_PATH-era path
    # failures when pytest's tmp_path is deeply nested.
    root = await asyncio.to_thread(
        Path(os.environ.get("JARVIS_COGNEE_TEST_ROOT", str(tmp_path))).resolve
    )
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    vault = root / "v"
    await asyncio.to_thread(vault.mkdir)
    note = vault / "memory-test.md"
    cfg = CogneeMemoryConfig(
        enabled=True,
        python_executable=python,
        worker_script=str(Path(__file__).parents[2] / "jarvis" / "memory" / "cognee_worker.py"),
        data_dir=str(root / "d"),
        dataset_name="jarvis-cognee-acceptance",
        timeout_s=240,
    )

    await asyncio.to_thread(
        note.write_text,
        "# Acceptance fact\nThe test observatory is located on Mars and uses a violet telescope.",
        encoding="utf-8",
    )
    first = CogneeMemoryAdapter(vault_root=vault, config=cfg)
    indexed = await first.index_path(note)
    assert indexed["indexed"] is True

    # A new adapter and a fresh worker process prove persisted retrieval.
    restarted = CogneeMemoryAdapter(vault_root=vault, config=cfg)
    original = await restarted.search("violet telescope Mars observatory", k=5)
    assert original and original[0]["source_path"] == "memory-test.md"
    source_id = original[0]["source_id"]

    await asyncio.to_thread(
        note.write_text,
        "# Acceptance fact\n"
        "The test observatory is located on Europa and uses a silver radio dish.",
        encoding="utf-8",
    )
    await restarted.search("Europa silver radio dish", k=5)  # search reconciles changed sources
    updated = await restarted.search("Europa silver radio dish", k=5)
    assert updated and updated[0]["source_id"] == source_id
    stale = await restarted.search("Mars violet telescope", k=5)
    assert not any("Mars" in hit["text"] or "violet telescope" in hit["text"] for hit in stale)

    await restarted.forget_path(note)
    forgotten = await CogneeMemoryAdapter(vault_root=vault, config=cfg).search(
        "Europa silver radio dish", k=5
    )
    assert not any(hit["source_id"] == source_id for hit in forgotten)
