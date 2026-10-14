"""Isolated, source-scoped Cognee bridge for authorized wiki memory.

Cognee is deliberately not imported by the Jarvis process. Each bounded JSON
request runs in a separately configured Python environment; native wiki/FTS
remains the source of truth and the fallback when Cognee is unavailable.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from jarvis.core.config import DATA_DIR, load_config
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)


def _resolve_data_root(configured_data: str) -> Path:
    return (
        Path(configured_data).expanduser().resolve()
        if configured_data
        else (DATA_DIR / "cognee").resolve()
    )


class CogneeUnavailable(RuntimeError):
    """Cognee is disabled, misconfigured, or its isolated worker failed."""


class CogneeMemoryAdapter:
    def __init__(self, *, vault_root: Path, config: Any | None = None) -> None:
        self._config = config or load_config().memory.cognee
        self._vault_root = vault_root.resolve()

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled)

    def _worker_settings(self) -> tuple[Path, Path, float]:
        if not self.enabled:
            raise CogneeUnavailable("Cognee memory is disabled")
        python = str(
            self._config.python_executable or os.getenv("JARVIS_COGNEE_PYTHON", "")
        ).strip()
        worker = str(self._config.worker_script or "").strip()
        if not worker:
            worker = str(Path(__file__).with_name("cognee_worker.py"))
        if not python:
            raise CogneeUnavailable(
                "Set [memory.cognee].python_executable to the isolated worker Python"
            )
        python_path, worker_path = (
            Path(python).expanduser().resolve(),
            Path(worker).expanduser().resolve(),
        )
        if not python_path.is_file() or not worker_path.is_file():
            raise CogneeUnavailable("Cognee worker runtime or script is missing")
        data_dir = str(self._config.data_dir or "").strip()
        root = (
            Path(data_dir).expanduser().resolve() if data_dir else (DATA_DIR / "cognee").resolve()
        )
        root.mkdir(parents=True, exist_ok=True)
        isolated_profile = root / "profile"
        isolated_profile.mkdir(parents=True, exist_ok=True)
        return python_path, worker_path, float(self._config.timeout_s)

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        python, worker, timeout = self._worker_settings()
        config = self._config
        data_dir = str(config.data_dir or "").strip()
        root = (
            Path(data_dir).expanduser().resolve() if data_dir else (DATA_DIR / "cognee").resolve()
        )
        root.mkdir(parents=True, exist_ok=True)
        isolated_profile = root / "profile"
        isolated_profile.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        # Cognee is explicitly local-only here. Do not inherit ambient hosted
        # model credentials into the isolated process.
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
                "LANGFUSE_SECRET_KEY",
            }:
                env.pop(key, None)
        env.update(
            {
                # LadybugDB's Windows extension manager writes under the process
                # home directory. Keep that cache inside Cognee's dedicated data
                # root instead of touching the user's real profile.
                "USERPROFILE": str(isolated_profile),
                "HOME": str(isolated_profile),
                "APPDATA": str(isolated_profile / "AppData" / "Roaming"),
                "LOCALAPPDATA": str(isolated_profile / "AppData" / "Local"),
                "HOMEDRIVE": root.drive,
                "HOMEPATH": str(isolated_profile)[len(root.drive) :],
                "DATA_ROOT_DIRECTORY": str(root / "data"),
                "SYSTEM_ROOT_DIRECTORY": str(root / "system"),
                "CACHE_ROOT_DIRECTORY": str(root / "cache"),
                "HF_HOME": str(isolated_profile / ".cache" / "huggingface"),
                "HUGGINGFACE_HUB_CACHE": str(isolated_profile / ".cache" / "huggingface" / "hub"),
                "COGNEE_LOGS_DIR": str(root / "logs"),
                "COGNEE_TELEMETRY_DISABLED": "true",
                "TELEMETRY_DISABLED": "1",
                "COGNEE_TRACING_ENABLED": "false",
                "GLINER_AUTO_INSTALL": "false",
                # Keep vector writes sequential in this isolated worker process.
                # Windows LanceDB file-copy commits are not safe when several
                # short-lived vector workers overlap; Cognee's graph subprocess
                # stays enabled because its Windows C API is not bundled in-process.
                "VECTOR_DB_SUBPROCESS_ENABLED": "false",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "OMP_NUM_THREADS": "2",
                "MKL_NUM_THREADS": "2",
                "PYTHONNOUSERSITE": "1",
            }
        )
        try:
            completed = subprocess.run(
                [str(python), str(worker)],
                input=json.dumps(
                    {
                        **payload,
                        "dataset": str(config.dataset_name),
                        "vault_root": str(self._vault_root),
                        "state_db": str(root / "source-state.sqlite3"),
                    }
                ),
                text=True,
                capture_output=True,
                timeout=timeout,
                cwd=str(root),
                env=env,
                check=False,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CogneeUnavailable(f"Cognee worker could not complete: {exc}") from exc
        if completed.returncode != 0:
            log.warning(
                "Cognee worker failed (exit=%s): %s", completed.returncode, completed.stderr[-2000:]
            )
            try:
                worker_error = json.loads(completed.stdout.strip().splitlines()[-1]).get("error")
            except (json.JSONDecodeError, IndexError):
                worker_error = None
            raise CogneeUnavailable(
                str(worker_error or f"Cognee worker exited {completed.returncode}")
            )
        try:
            result = json.loads(completed.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError) as exc:
            raise CogneeUnavailable("Cognee worker returned an invalid response") from exc
        if not result.get("ok"):
            raise CogneeUnavailable(str(result.get("error", "Cognee operation failed")))
        return result

    async def index_path(self, path: Path) -> dict[str, Any]:
        resolved = await asyncio.to_thread(path.resolve)
        is_file = await asyncio.to_thread(resolved.is_file)
        if not resolved.is_relative_to(self._vault_root) or not is_file:
            raise ValueError(
                "Cognee indexing is restricted to existing files inside the wiki vault"
            )
        if resolved.suffix.lower() != ".md":
            raise ValueError("Only Markdown wiki sources may be indexed")
        content = await asyncio.to_thread(resolved.read_text, encoding="utf-8", errors="replace")
        if not content.strip():
            raise ValueError("Empty wiki pages are not indexed")
        from jarvis.memory.wiki.secret_guard import contains_secret

        if contains_secret(content):
            raise ValueError("Sensitive content is excluded from semantic indexing")
        source_id = hashlib.sha256(
            resolved.relative_to(self._vault_root).as_posix().encode("utf-8")
        ).hexdigest()
        return await asyncio.to_thread(
            self._request,
            {
                "op": "index",
                "source_id": source_id,
                "source_path": resolved.relative_to(self._vault_root).as_posix(),
                "content": content,
            },
        )

    async def search(self, query: str, *, k: int = 5) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        config = self._config
        configured_data = str(config.data_dir or "").strip()
        root = await asyncio.to_thread(_resolve_data_root, configured_data)
        denied_sources = await asyncio.to_thread(self._sources_with_unsafe_content, root)
        result = await asyncio.to_thread(
            self._request,
            {
                "op": "search",
                "query": query.strip(),
                "k": max(1, min(int(k), 10)),
                "deny_source_ids": denied_sources,
            },
        )
        return list(result.get("results", []))

    def _sources_with_unsafe_content(self, root: Path) -> list[str]:
        """Use the authoritative wiki secret guard before worker reconciliation."""
        state_db = root / "source-state.sqlite3"
        if not state_db.is_file():
            return []
        try:
            with sqlite3.connect(state_db, timeout=2) as conn:
                rows = conn.execute(
                    "SELECT source_id,source_path FROM sources WHERE status='active'"
                ).fetchall()
        except sqlite3.Error as exc:
            raise CogneeUnavailable(
                f"Cognee source state could not be checked safely: {exc}"
            ) from exc
        from jarvis.memory.wiki.secret_guard import contains_secret

        denied: list[str] = []
        for source_id, source_path in rows:
            relative = Path(source_path)
            candidate = (self._vault_root / relative).resolve()
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not candidate.is_relative_to(self._vault_root)
            ):
                denied.append(str(source_id))
                continue
            try:
                content = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                denied.append(str(source_id))
                continue
            if contains_secret(content):
                denied.append(str(source_id))
        return denied

    async def forget_path(self, path: Path) -> dict[str, Any]:
        resolved = await asyncio.to_thread(path.resolve)
        if not resolved.is_relative_to(self._vault_root):
            raise ValueError("Cognee deletion is restricted to the wiki vault")
        source_id = hashlib.sha256(
            resolved.relative_to(self._vault_root).as_posix().encode("utf-8")
        ).hexdigest()
        return await asyncio.to_thread(
            self._request,
            {
                "op": "forget",
                "source_id": source_id,
                "source_path": resolved.relative_to(self._vault_root).as_posix(),
            },
        )


def build_cognee_adapter(vault_root: Path) -> CogneeMemoryAdapter:
    return CogneeMemoryAdapter(vault_root=vault_root)
