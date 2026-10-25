"""Isolated, source-scoped Cognee bridge for authorized memory.

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
        isolated_tmp = root / "tmp"
        isolated_tmp.mkdir(parents=True, exist_ok=True)
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
        isolated_tmp = root / "tmp"
        isolated_tmp.mkdir(parents=True, exist_ok=True)
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
                # FastEmbed defaults to tempfile.gettempdir(), which can point
                # at a restricted Windows app-container temp directory. Keep
                # both temporary downloads and the final model cache inside the
                # adapter's own data root; this also avoids touching the user's
                # global Hugging Face/FastEmbed caches.
                "TEMP": str(isolated_tmp),
                "TMP": str(isolated_tmp),
                "FASTEMBED_CACHE_PATH": str(root / "fastembed_cache"),
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
                "source_type": "wiki",
            },
        )

    @staticmethod
    def render_conversation(messages: list[dict[str, Any]]) -> str:
        """Build a compact, provenance-preserving transcript from safe roles."""
        parts: list[str] = []
        total_chars = 0
        for message in messages[-300:]:
            role = str(message.get("role", ""))
            text = str(message.get("text", "")).strip()
            if role not in {"user", "assistant"} or not text:
                continue
            text = text[:12_000]
            timestamp = str(message.get("timestamp_ns", ""))
            parts.append(f"[{timestamp}] {role}: {text}")
            total_chars += len(parts[-1])
            if total_chars >= 100_000:
                break
        return "\n\n".join(parts)[:100_000]

    async def index_conversation(
        self, thread_id: str, messages: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Index one user-selected conversation thread with a stable source id."""
        thread_id = str(thread_id).strip()
        if not thread_id or len(thread_id) > 256:
            raise ValueError("a valid conversation thread id is required")
        content = self.render_conversation(messages)
        if not content:
            raise ValueError("conversation has no user or assistant messages to index")
        thread_key = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
        source_id = hashlib.sha256(f"conversation:{thread_id}".encode()).hexdigest()
        source_path = f"conversations/{thread_key}.md"
        from jarvis.memory.wiki.secret_guard import contains_secret

        if contains_secret(content):
            if self.enabled:
                # If an already-indexed transcript was edited to contain
                # sensitive text, revoke its old derived chunks before
                # refusing the new version.
                await asyncio.to_thread(
                    self._request,
                    {
                        "op": "forget",
                        "source_id": source_id,
                        "source_path": source_path,
                        "source_type": "conversation",
                    },
                )
            raise ValueError("sensitive content is excluded from semantic indexing")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        root = _resolve_data_root(str(self._config.data_dir or "").strip())
        state_db = root / "source-state.sqlite3"
        if state_db.is_file():
            try:
                with sqlite3.connect(state_db, timeout=2) as conn:
                    existing = conn.execute(
                        "SELECT content_hash,status,source_type FROM sources WHERE source_id=?",
                        (source_id,),
                    ).fetchone()
                if existing == (content_hash, "active", "conversation"):
                    return {"indexed": False, "unchanged": True, "source_id": source_id,
                            "source_path": source_path, "content_hash": content_hash}
            except sqlite3.Error:
                # The isolated worker owns schema migration and reports any
                # genuine store failure through the normal request boundary.
                pass
        return await asyncio.to_thread(
            self._request,
            {
                "op": "index",
                "source_id": source_id,
                "source_path": source_path,
                "source_type": "conversation",
                "source_ref": thread_id,
                "content": content,
            },
        )

    async def forget_conversation(self, thread_id: str) -> dict[str, Any]:
        thread_id = str(thread_id).strip()
        if not thread_id or len(thread_id) > 256:
            raise ValueError("a valid conversation thread id is required")
        thread_key = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
        source_id = hashlib.sha256(f"conversation:{thread_id}".encode()).hexdigest()
        return await asyncio.to_thread(
            self._request,
            {
                "op": "forget",
                "source_id": source_id,
                "source_path": f"conversations/{thread_key}.md",
                "source_type": "conversation",
            },
        )

    def list_conversation_sources(self) -> list[dict[str, str]]:
        """List only active conversation source identities for reconciliation."""
        if not self.enabled:
            return []
        configured_data = str(self._config.data_dir or "").strip()
        state_db = _resolve_data_root(configured_data) / "source-state.sqlite3"
        if not state_db.is_file():
            return []
        try:
            with sqlite3.connect(state_db, timeout=2) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(sources)")}
                required = {"source_id", "source_path", "content_hash", "status"}
                typed = {"source_type", "source_ref"}
                if not required.issubset(columns) or (
                    typed & columns and not typed.issubset(columns)
                ):
                    raise CogneeUnavailable(
                        "Cognee source state could not be checked safely: "
                        "incomplete source registry schema"
                    )
                # Before conversation support, every record was a wiki note.
                # Recognize that complete legacy schema without hiding damage.
                if not typed & columns:
                    return []
                rows = conn.execute(
                    "SELECT source_id,source_path,source_ref,content_hash "
                    "FROM sources WHERE status='active' AND source_type='conversation'"
                ).fetchall()
        except sqlite3.Error as exc:
            raise CogneeUnavailable(
                f"Cognee source state could not be checked safely: {exc}"
            ) from exc
        return [
            {
                "source_id": str(row[0]),
                "source_path": str(row[1]),
                "thread_id": str(row[2]),
                "content_hash": str(row[3]),
            }
            for row in rows
        ]

    async def search_conversations(self, query: str, *, k: int = 5) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        result = await asyncio.to_thread(
            self._request,
            {
                "op": "search",
                "query": query.strip(),
                "k": max(1, min(int(k), 10)),
                "source_types": ["conversation"],
            },
        )
        return list(result.get("results", []))

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
                "source_types": ["wiki"],
            },
        )
        return list(result.get("results", []))

    def list_active_sources(self) -> list[dict[str, str]]:
        """Return only current, authorized source records for the memory map.

        The registry is derived state. A row is shown only while its source is
        still inside the vault, unchanged since indexing, and passes the same
        secret guard used by semantic retrieval.
        """
        if not self.enabled:
            return []
        configured_data = str(self._config.data_dir or "").strip()
        root = _resolve_data_root(configured_data)
        state_db = root / "source-state.sqlite3"
        if not state_db.is_file():
            return []
        try:
            with sqlite3.connect(state_db, timeout=2) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(sources)")}
                query = (
                    "SELECT source_id,source_path,content_hash FROM sources WHERE status='active'"
                )
                # Registries created before conversation support contain only wiki sources.
                if "source_type" in columns:
                    query += " AND source_type='wiki'"
                rows = conn.execute(query).fetchall()
        except sqlite3.Error as exc:
            raise CogneeUnavailable(
                f"Cognee source state could not be checked safely: {exc}"
            ) from exc

        from jarvis.memory.wiki.secret_guard import contains_secret

        visible: list[dict[str, str]] = []
        for source_id, source_path, content_hash in rows:
            relative = Path(source_path)
            if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".md":
                continue
            candidate = (self._vault_root / relative).resolve()
            if not candidate.is_relative_to(self._vault_root) or not candidate.is_file():
                continue
            try:
                content = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if hashlib.sha256(content.encode("utf-8")).hexdigest() != content_hash:
                continue
            if contains_secret(content):
                continue
            visible.append(
                {
                    "source_id": str(source_id),
                    "source_path": relative.as_posix(),
                }
            )
        return visible

    def _sources_with_unsafe_content(self, root: Path) -> list[str]:
        """Use the authoritative wiki secret guard before worker reconciliation."""
        state_db = root / "source-state.sqlite3"
        if not state_db.is_file():
            return []
        try:
            with sqlite3.connect(state_db, timeout=2) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(sources)")}
                query = "SELECT source_id,source_path FROM sources WHERE status='active'"
                # Apply secret checks before the worker migrates a legacy wiki registry.
                if "source_type" in columns:
                    query += " AND source_type='wiki'"
                rows = conn.execute(query).fetchall()
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
                "source_type": "wiki",
            },
        )


def build_cognee_adapter(vault_root: Path) -> CogneeMemoryAdapter:
    return CogneeMemoryAdapter(vault_root=vault_root)
