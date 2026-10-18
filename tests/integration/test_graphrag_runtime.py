"""Real GraphRAG acceptance test for a bounded local multi-document corpus.

Run with JARVIS_GRAPHRAG_PYTHON pointing at the isolated GraphRAG Python and
JARVIS_GRAPHRAG_API_BASE pointing at a local OpenAI-compatible chat endpoint.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jarvis.core.config import GraphRagMemoryConfig
from jarvis.memory.graphrag_adapter import GraphRagMemoryAdapter


@pytest.mark.asyncio
async def test_local_graphrag_multi_document_acceptance(tmp_path: Path) -> None:
    python = os.environ.get("JARVIS_GRAPHRAG_PYTHON")
    if not python:
        pytest.skip("set JARVIS_GRAPHRAG_PYTHON to the isolated GraphRAG Python")

    vault = tmp_path / "authorized-vault"
    vault.mkdir()
    corpus = {
        "austin-drone.txt": (
            "Alice Morgan leads the Kestrel flood-rescue drone program at Acme Robotics "
            "in Austin, Texas.\n"
        ),
        "university-partner.txt": (
            "Acme Robotics partners with Texas State University in San Marcos to develop "
            "the Kestrel drone. Professor Luis Chen leads the university team.\n"
        ),
        "fire-department-use.txt": (
            "The Austin Fire Department deploys Kestrel drones for flood rescue. "
            "Alice Morgan coordinates the pilot with the department.\n"
        ),
    }
    sources = []
    for name, text in corpus.items():
        path = vault / name
        path.write_text(text, encoding="utf-8")
        sources.append(path)

    worker = Path(__file__).parents[2] / "jarvis" / "memory" / "graphrag_worker.py"
    config = GraphRagMemoryConfig(
        enabled=True,
        python_executable=python,
        worker_script=str(worker),
        data_dir=str(tmp_path / "graphrag-data"),
        api_base=os.environ.get("JARVIS_GRAPHRAG_API_BASE", "http://127.0.0.1:11440/v1"),
        chat_model=os.environ.get("JARVIS_GRAPHRAG_CHAT_MODEL", "qwen3-vl-8b"),
        embedding_model="BAAI/bge-small-en-v1.5",
        timeout_s=1800,
        max_sources=10,
        max_source_chars=40_000,
    )
    adapter = GraphRagMemoryAdapter(vault_root=vault, config=config)

    indexed = await adapter.index_paths(sources)
    assert indexed["indexed"] == len(sources)
    assert len(indexed["source_ids"]) == len(sources)
    project = Path(indexed["workspace"])
    for output in (
        "entities.parquet",
        "relationships.parquet",
        "community_reports.parquet",
        "text_units.parquet",
        "vectors.sqlite3",
    ):
        assert (project / "output" / output).is_file()
    assert (project / "index.complete").is_file()

    status = await adapter.status()
    assert status["source_count"] == len(sources)
    result = await adapter.search(
        "Which university partnered with Acme Robotics on Kestrel, "
        "and which rescue service uses it?"
    )
    assert result
    assert {item["source_path"] for item in result}.issubset(corpus)
    assert {item["source_id"] for item in result}.issubset(set(indexed["source_ids"]))
    assert any(
        item["source_path"] in {"university-partner.txt", "fire-department-use.txt"}
        for item in result
    )

    global_result = await adapter.global_search(
        "Explain the cross-document relationship among Alice Morgan, Acme Robotics, "
        "Texas State University, the Kestrel drone, and the Austin Fire Department."
    )
    assert global_result["answer"].strip()
    assert global_result["communities"]
    assert global_result["source_scope"] == "full_indexed_corpus"
    assert {item["source_id"] for item in global_result["sources"]} == set(
        indexed["source_ids"]
    )
    assert {item["source_path"] for item in global_result["sources"]} == set(corpus)
