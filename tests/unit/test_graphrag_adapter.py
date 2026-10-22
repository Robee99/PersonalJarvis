from __future__ import annotations

import http.client
import json
import os
import sys
import types
from pathlib import Path

import pytest

from jarvis.core.config import GraphRagMemoryConfig
from jarvis.memory.graphrag_adapter import (
    GraphRagMemoryAdapter,
    GraphRagUnavailable,
    _is_loopback_api_base,
)
from jarvis.memory.graphrag_worker import _local_embedding_api


def test_graphrag_is_opt_in_and_requires_local_endpoint() -> None:
    config = GraphRagMemoryConfig()
    assert config.enabled is False
    assert config.api_base == "http://127.0.0.1:11440/v1"
    assert _is_loopback_api_base("http://127.0.0.1:1234/v1")
    assert _is_loopback_api_base("http://localhost:1234/v1")
    assert not _is_loopback_api_base("https://provider.example/v1")
    assert not _is_loopback_api_base("http://192.168.1.10:1234/v1")


def test_local_embedding_api_uses_loopback_and_openai_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Vector(list):
        def tolist(self) -> list[float]:
            return list(self)

    class FakeTextEmbedding:
        def __init__(
            self, *, model_name: str, cache_dir: str, threads: int, providers: list[str]
        ) -> None:
            assert model_name == "test-model"
            assert cache_dir
            assert threads == 2
            assert providers == ["CPUExecutionProvider"]

        def embed(self, inputs: list[str]) -> list[Vector]:
            return [Vector([float(len(item)), 1.0]) for item in inputs]

    monkeypatch.setitem(
        sys.modules,
        "fastembed",
        types.SimpleNamespace(TextEmbedding=FakeTextEmbedding),
    )
    with _local_embedding_api("test-model", Path("embedding-cache")) as api_base:
        assert api_base.startswith("http://127.0.0.1:")
        port = int(api_base.rsplit(":", 1)[1].split("/", 1)[0])
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        connection.request(
            "POST",
            "/v1/embeddings",
            body=b'{"input":["one","two"]}',
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        assert response.status == 200
    assert payload["object"] == "list"
    assert [item["index"] for item in payload["data"]] == [0, 1]
    assert payload["data"][0]["embedding"] == [3.0, 1.0]


@pytest.mark.asyncio
async def test_graphrag_refuses_cloud_endpoint_before_starting_worker(tmp_path: Path) -> None:
    config = GraphRagMemoryConfig(
        enabled=True,
        python_executable="python",
        worker_script="worker.py",
        api_base="https://api.example/v1",
        data_dir=str(tmp_path / "graph"),
    )
    adapter = GraphRagMemoryAdapter(vault_root=tmp_path / "vault", config=config)

    with pytest.raises(GraphRagUnavailable, match="localhost"):
        await adapter.status()


@pytest.mark.asyncio
async def test_isolated_graphrag_worker_persists_empty_status(tmp_path: Path) -> None:
    python = os.environ.get("JARVIS_GRAPHRAG_PYTHON")
    if not python:
        pytest.skip("set JARVIS_GRAPHRAG_PYTHON to the isolated GraphRAG Python")
    worker = Path(__file__).parents[2] / "jarvis" / "memory" / "graphrag_worker.py"
    config = GraphRagMemoryConfig(
        enabled=True,
        python_executable=python,
        worker_script=str(worker),
        data_dir=str(tmp_path / "graph"),
    )
    adapter = GraphRagMemoryAdapter(vault_root=tmp_path / "vault", config=config)

    status = await adapter.status()

    assert status["source_count"] == 0
    assert status["workspace"] is None
