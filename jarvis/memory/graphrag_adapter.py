"""Isolated, local-only GraphRAG bridge for authorized wiki documents.

GraphRAG is an optional corpus-level retrieval layer. Native wiki search stays
the source of truth; the worker is a separate Python environment and receives
only paths that are already inside the configured wiki vault.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jarvis.core.config import DATA_DIR, load_config
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)


class GraphRagUnavailable(RuntimeError):
    """GraphRAG is disabled, misconfigured, or its isolated worker failed."""


def _is_loopback_api_base(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.port is not None
        and not parsed.username
        and not parsed.password
    )


class GraphRagMemoryAdapter:
    """Bounded subprocess adapter; GraphRAG is never imported by JARVIS."""

    def __init__(self, *, vault_root: Path, config: Any | None = None) -> None:
        self._config = config or load_config().memory.graphrag
        self._vault_root = vault_root.resolve()

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled)

    def _worker_settings(self) -> tuple[Path, Path, Path, float]:
        if not self.enabled:
            raise GraphRagUnavailable("GraphRAG memory is disabled")
        api_base = str(self._config.api_base or "").strip()
        if not _is_loopback_api_base(api_base):
            raise GraphRagUnavailable(
                "GraphRAG accepts only an HTTP model endpoint bound to localhost"
            )
        python = str(self._config.python_executable or "").strip()
        worker = str(self._config.worker_script or "").strip()
        if not python:
            raise GraphRagUnavailable(
                "Set [memory.graphrag].python_executable to the isolated GraphRAG Python"
            )
        if not worker:
            worker = str(Path(__file__).with_name("graphrag_worker.py"))
        data_dir = str(self._config.data_dir or "").strip()
        root = (
            Path(data_dir).expanduser().resolve() if data_dir else (DATA_DIR / "graphrag").resolve()
        )
        python_path = Path(python).expanduser().resolve()
        worker_path = Path(worker).expanduser().resolve()
        if not python_path.is_file() or not worker_path.is_file():
            raise GraphRagUnavailable("GraphRAG worker runtime or script is missing")
        return python_path, worker_path, root, float(self._config.timeout_s)

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        python, worker, root, timeout = self._worker_settings()
        root.mkdir(parents=True, exist_ok=True)
        isolated_home = root / "profile"
        isolated_home.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        # Do not let GraphRAG/LiteLLM inherit any hosted provider credentials.
        for key in tuple(env):
            upper = key.upper()
            if upper.endswith("_API_KEY") or upper in {
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "GOOGLE_API_KEY",
                "GEMINI_API_KEY",
                "GROQ_API_KEY",
                "XAI_API_KEY",
                "MISTRAL_API_KEY",
                "LLM_API_KEY",
                "AZURE_OPENAI_API_KEY",
            }:
                env.pop(key, None)
        env.update(
            {
                # GraphRAG's OpenAI-compatible client requires a non-empty key
                # even for many local servers. This sentinel never leaves the
                # loopback API base validated above.
                "GRAPHRAG_API_KEY": "jarvis-local-only",
                "OPENAI_API_KEY": "jarvis-local-only",
                "USERPROFILE": str(isolated_home),
                "HOME": str(isolated_home),
                "APPDATA": str(isolated_home / "AppData" / "Roaming"),
                "LOCALAPPDATA": str(isolated_home / "AppData" / "Local"),
                "HF_HOME": str(isolated_home / "huggingface"),
                "HF_HUB_CACHE": str(isolated_home / "huggingface" / "hub"),
                "HF_XET_CACHE": str(isolated_home / "huggingface" / "xet"),
                "PYTHONNOUSERSITE": "1",
                "TOKENIZERS_PARALLELISM": "false",
                "OMP_NUM_THREADS": "2",
                "MKL_NUM_THREADS": "2",
            }
        )
        body = {
            **payload,
            "vault_root": str(self._vault_root),
            "data_dir": str(root),
            "api_base": str(self._config.api_base),
            "chat_model": str(self._config.chat_model),
            "embedding_model": str(self._config.embedding_model),
            "timeout_s": timeout,
            "max_sources": int(self._config.max_sources),
            "max_source_chars": int(self._config.max_source_chars),
        }
        try:
            completed = subprocess.run(
                [str(python), str(worker)],
                input=json.dumps(body),
                text=True,
                capture_output=True,
                timeout=timeout,
                cwd=str(root),
                env=env,
                check=False,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GraphRagUnavailable(f"GraphRAG worker could not complete: {exc}") from exc
        try:
            result = json.loads(completed.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError) as exc:
            if completed.returncode != 0:
                log.warning(
                    "GraphRAG worker failed (exit=%s): %s",
                    completed.returncode,
                    completed.stderr[-2000:],
                )
            raise GraphRagUnavailable("GraphRAG worker returned an invalid response") from exc
        if not result.get("ok"):
            if completed.returncode != 0:
                log.warning(
                    "GraphRAG worker failed (exit=%s): %s",
                    completed.returncode,
                    completed.stderr[-2000:],
                )
            raise GraphRagUnavailable(str(result.get("error", "GraphRAG operation failed")))
        if completed.returncode != 0:
            # The isolated runtime can finish the operation, emit its JSON
            # result, and then exit with Windows STATUS_INVALID_HANDLE during
            # native-library shutdown. Trust only the already-serialized
            # positive result; keep the teardown anomaly visible in logs.
            log.warning(
                "GraphRAG worker returned success JSON but exited %s during shutdown",
                completed.returncode,
            )
        return result

    async def index_path(self, path: Path) -> dict[str, Any]:
        resolved = await asyncio.to_thread(path.resolve)
        if not resolved.is_relative_to(self._vault_root):
            raise GraphRagUnavailable("GraphRAG source must be inside the authorized wiki vault")
        return await asyncio.to_thread(self._request, {"op": "index", "path": str(resolved)})

    async def index_paths(self, paths: list[Path]) -> dict[str, Any]:
        """Ingest a bounded set of authorized sources and build the corpus once."""
        if not paths or len(paths) > int(self._config.max_sources):
            raise GraphRagUnavailable("GraphRAG batch must contain 1 to max_sources files")
        resolved = await asyncio.gather(*(asyncio.to_thread(path.resolve) for path in paths))
        if any(not path.is_relative_to(self._vault_root) for path in resolved):
            raise GraphRagUnavailable("GraphRAG sources must be inside the authorized wiki vault")
        return await asyncio.to_thread(
            self._request,
            {"op": "index_batch", "paths": [str(path) for path in resolved]},
        )

    async def forget_path(self, path: Path) -> dict[str, Any]:
        resolved = await asyncio.to_thread(path.resolve)
        if not resolved.is_relative_to(self._vault_root):
            raise GraphRagUnavailable("GraphRAG source must be inside the authorized wiki vault")
        return await asyncio.to_thread(self._request, {"op": "forget", "path": str(resolved)})

    async def search(self, query: str, *, k: int = 5) -> list[dict[str, str]]:
        clean_query = str(query).strip()
        if not clean_query:
            return []
        result = await asyncio.to_thread(
            self._request,
            {"op": "search", "query": clean_query, "k": max(1, min(int(k), 10))},
        )
        return [
            {
                "text": str(item["text"]),
                "source_path": str(item["source_path"]),
                "source_id": str(item["source_id"]),
            }
            for item in result.get("hits", [])
            if item.get("text") and item.get("source_path") and item.get("source_id")
        ]

    async def global_search(self, query: str) -> dict[str, Any]:
        clean_query = str(query).strip()
        if not clean_query:
            return {"answer": "", "sources": []}
        return await asyncio.to_thread(self._request, {"op": "global_search", "query": clean_query})

    async def status(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._request, {"op": "status"})


def build_graphrag_adapter(vault_root: Path) -> GraphRagMemoryAdapter:
    return GraphRagMemoryAdapter(vault_root=vault_root)
