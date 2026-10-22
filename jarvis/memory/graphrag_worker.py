"""One-shot GraphRAG worker. This module runs only in the isolated GraphRAG venv."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import http.server
import io
import json
import re
import sqlite3
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def _loopback(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.port is not None
        and not parsed.username
        and not parsed.password
    )


def _state_db(root: Path) -> sqlite3.Connection:
    db = sqlite3.connect(root / "sources.sqlite3")
    db.row_factory = sqlite3.Row
    db.execute(
        """CREATE TABLE IF NOT EXISTS sources (
             source_id TEXT PRIMARY KEY, source_path TEXT NOT NULL,
             content TEXT NOT NULL, content_hash TEXT NOT NULL)"""
    )
    db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.commit()
    return db


def _index_project(project: Path) -> None:
    """Run GraphRAG in the already-isolated worker process."""
    from graphrag.api import build_index
    from graphrag.callbacks.console_workflow_callbacks import ConsoleWorkflowCallbacks
    from graphrag.config.enums import IndexingMethod
    from graphrag.config.load_config import load_config
    from graphrag.logger.standard_logging import init_loggers
    from graphrag_sqlite_store import register_jarvis_sqlite_vector_store

    register_jarvis_sqlite_vector_store()
    config = load_config(root_dir=project)
    init_loggers(config=config, verbose=False)
    outputs = asyncio.run(
        build_index(
            config=config,
            method=IndexingMethod.Standard,
            is_update_run=False,
            callbacks=[ConsoleWorkflowCallbacks(verbose=False)],
            verbose=False,
        )
    )
    failures = [
        f"{getattr(output, 'workflow', 'workflow')}: {output.error}"
        for output in outputs
        if getattr(output, "error", None) is not None
    ]
    if failures:
        raise RuntimeError("GraphRAG workflow errors: " + "; ".join(failures))


@contextmanager
def _local_embedding_api(model_name: str, cache_dir: Path):
    """Expose isolated CPU FastEmbed as a loopback OpenAI embeddings endpoint."""
    from fastembed import TextEmbedding

    model = TextEmbedding(
        model_name=model_name,
        cache_dir=str(cache_dir),
        threads=2,
        providers=["CPUExecutionProvider"],
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path != "/v1/embeddings":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 2_000_000:
                    raise ValueError("request body size is outside the allowed range")
                request = json.loads(self.rfile.read(length))
                raw_inputs = request.get("input")
                inputs = [raw_inputs] if isinstance(raw_inputs, str) else raw_inputs
                if (
                    not isinstance(inputs, list)
                    or not inputs
                    or len(inputs) > 64
                    or any(not isinstance(item, str) or len(item) > 50_000 for item in inputs)
                ):
                    raise ValueError("input must contain 1-64 text items of at most 50000 chars")
                vectors = [vector.tolist() for vector in model.embed(inputs)]
                payload = {
                    "object": "list",
                    "data": [
                        {"object": "embedding", "index": index, "embedding": vector}
                        for index, vector in enumerate(vectors)
                    ],
                    "model": model_name,
                    "usage": {"prompt_tokens": 0, "total_tokens": 0},
                }
                encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                encoded = json.dumps(
                    {"error": {"message": str(exc), "type": "invalid_request_error"}}
                ).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, name="jarvis-graphrag-embed", daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _settings(project: Path, body: dict, embedding_api_base: str) -> None:
    import yaml

    path = project / "settings.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    api_base = str(body["api_base"]).rstrip("/")
    for section, model_name, model_api_base in (
        ("completion_models", body["chat_model"], api_base),
                ("embedding_models", body["embedding_model"], embedding_api_base),
    ):
        model = data[section][next(iter(data[section]))]
        model.update(
            {
                "type": "litellm",
                "model_provider": "openai",
                "model": model_name,
                "api_base": model_api_base,
                "api_key": "${GRAPHRAG_API_KEY}",
                "retry": {"type": "immediate", "max_retries": 2},
            }
        )
    data["input"] = {
        "type": "text",
        # GraphRAG expands ${ENV_VAR} with string.Template. Avoid a trailing
        # regex '$' here because it is parsed as an invalid env placeholder.
        "file_pattern": r".*\.txt",
        "encoding": "utf-8",
    }
    data["input_storage"]["base_dir"] = "input"
    data["output_storage"]["base_dir"] = "output"
    data["reporting"]["base_dir"] = "logs"
    data["cache"]["storage"]["base_dir"] = "cache"
    data["chunking"]["size"] = 512
    data["chunking"]["overlap"] = 64
    data["extract_graph"]["entity_types"] = [
        "organization",
        "person",
        "geo",
        "product",
        "program",
        "event",
    ]
    data["summarize_descriptions"]["max_length"] = 150
    data["community_reports"]["max_length"] = 500
    data["community_reports"]["max_input_length"] = 4000
    data["vector_store"] = {
        "type": "jarvis_sqlite",
        "db_uri": str(project / "output" / "vectors.sqlite3"),
        "vector_size": 384,
    }
    data["extract_graph"]["max_gleanings"] = 0
    data["extract_claims"]["enabled"] = False
    data["snapshots"] = {"graphml": False, "embeddings": False}
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    (project / ".env").write_text("GRAPHRAG_API_KEY=jarvis-local-only\n", encoding="utf-8")


def _active(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        db.execute(
            "SELECT source_id, source_path, content, content_hash FROM sources ORDER BY source_id"
        )
    )


def _build_index(
    root: Path, db: sqlite3.Connection, body: dict, embedding_api_base: str
) -> dict:
    sources = _active(db)
    if not sources:
        db.execute("DELETE FROM metadata WHERE key='current_project'")
        db.commit()
        return {"indexed": 0, "workspace": None}
    max_sources = int(body["max_sources"])
    if len(sources) > max_sources:
        raise ValueError(f"Source count {len(sources)} exceeds configured limit {max_sources}")
    digest_input = "\n".join(
        f"{row['source_id']}:{row['content_hash']}" for row in sources
    )
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:20]
    project = root / "indexes" / digest
    try:
        if not (project / "index.complete").is_file():
            project.parent.mkdir(parents=True, exist_ok=True)
            if not (project / "settings.yaml").is_file():
                from graphrag.cli.initialize import initialize_project_at

                initialize_project_at(
                    path=project,
                    force=False,
                    model=str(body["chat_model"]),
                    embedding_model=str(body["embedding_model"]),
                )
            _settings(project, body, embedding_api_base)
            input_dir = project / "input"
            input_dir.mkdir(parents=True, exist_ok=True)
            max_chars = int(body["max_source_chars"])
            for row in sources:
                content = str(row["content"])
                if len(content) > max_chars:
                    raise ValueError(
                        f"Source exceeds configured character limit: {row['source_path']}"
                    )
                rel = Path(row["source_path"]).as_posix()
                payload = (
                    f"JARVIS_SOURCE_ID={row['source_id']}\nJARVIS_SOURCE_PATH={rel}\n\n{content}"
                )
                (input_dir / f"{row['source_id']}.txt").write_text(payload, encoding="utf-8")
            _index_project(project)
            required_outputs = (
                "entities.parquet",
                "relationships.parquet",
                "community_reports.parquet",
                "text_units.parquet",
            )
            missing = [
                name for name in required_outputs
                if not (project / "output" / name).is_file()
            ]
            if missing or not (project / "output" / "vectors.sqlite3").is_file():
                raise RuntimeError(f"GraphRAG index omitted required outputs: {missing}")
            (project / "index.complete").write_text(digest, encoding="ascii")
    except Exception:
        # Never leave a stale index available after a failed update/delete.
        db.execute("DELETE FROM metadata WHERE key='current_project'")
        db.commit()
        raise
    db.execute(
        "INSERT OR REPLACE INTO metadata(key,value) VALUES('current_project',?)",
        (str(project),),
    )
    db.commit()
    return {"indexed": len(sources), "workspace": str(project), "corpus_id": digest}


def _query(project: Path, body: dict, db: sqlite3.Connection) -> dict:
    from graphrag_sqlite_store import register_jarvis_sqlite_vector_store

    register_jarvis_sqlite_vector_store()
    from graphrag.cli.query import run_local_search

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        answer, context = run_local_search(
            data_dir=None,
            root_dir=project,
            community_level=2,
            response_type="Concise answer with evidence",
            streaming=False,
            query=str(body["query"]),
            verbose=False,
        )
    active = {row["source_id"]: row for row in _active(db)}
    hits: list[dict[str, str]] = []
    source_table = context.get("sources") if isinstance(context, dict) else None
    if source_table is not None:
        for record in source_table.to_dict(orient="records"):
            chunk = str(record.get("text", ""))
            id_match = re.search(r"JARVIS_SOURCE_ID=([0-9a-f]{64})", chunk)
            if not id_match:
                continue
            source_id = id_match.group(1)
            source = active.get(source_id)
            if source is None:
                continue
            hits.append(
                {
                    "text": chunk[:4000],
                    "source_id": source_id,
                    "source_path": source["source_path"],
                }
            )
            if len(hits) >= int(body.get("k", 5)):
                break
    return {"answer": str(answer), "hits": hits}


def _community_source_links(
    project: Path,
    reports: list[dict[str, Any]],
    db: sqlite3.Connection,
) -> dict[int, list[dict[str, str]]]:
    """Join GraphRAG's community -> text unit -> document IDs to active notes."""
    import pyarrow.parquet as pq
    from graphrag_provenance import map_community_sources

    output_dir = project / "output"
    communities_path = output_dir / "communities.parquet"
    text_units_path = output_dir / "text_units.parquet"
    documents_path = output_dir / "documents.parquet"
    if not all(path.is_file() for path in (communities_path, text_units_path, documents_path)):
        return {}

    target_community_ids = {
        int(row["community"]) for row in reports if row.get("community") is not None
    }
    if not target_community_ids:
        return {}

    community_to_units: dict[int, set[str]] = {}
    unit_to_documents: dict[str, str] = {}
    document_to_sources: dict[str, str] = {}
    max_unit_ids = 20_000
    total_unit_ids = 0

    community_file = pq.ParquetFile(communities_path)
    for group_index in range(community_file.num_row_groups):
        rows = community_file.read_row_group(
            group_index, columns=["community", "text_unit_ids"]
        ).to_pylist()
        for row in rows:
            community_id = int(row["community"])
            if community_id not in target_community_ids:
                continue
            units = community_to_units.setdefault(community_id, set())
            for unit_id in row.get("text_unit_ids") or []:
                if total_unit_ids >= max_unit_ids:
                    break
                if unit_id not in units:
                    units.add(str(unit_id))
                    total_unit_ids += 1
        if total_unit_ids >= max_unit_ids:
            break

    required_units = {
        unit_id for unit_ids in community_to_units.values() for unit_id in unit_ids
    }
    if not required_units:
        return {}

    units_file = pq.ParquetFile(text_units_path)
    for group_index in range(units_file.num_row_groups):
        rows = units_file.read_row_group(group_index, columns=["id", "document_id"]).to_pylist()
        for row in rows:
            unit_id = str(row["id"])
            if unit_id in required_units and row.get("document_id"):
                unit_to_documents[unit_id] = str(row["document_id"])

    required_documents = set(unit_to_documents.values())
    documents_file = pq.ParquetFile(documents_path)
    for group_index in range(documents_file.num_row_groups):
        rows = documents_file.read_row_group(group_index, columns=["id", "title"]).to_pylist()
        for row in rows:
            document_id = str(row["id"])
            if document_id not in required_documents:
                continue
            # The builder names each input file with its stable JARVIS source ID.
            title = Path(str(row.get("title") or "")).name
            match = re.fullmatch(r"([0-9a-f]{64})\.txt", title, flags=re.IGNORECASE)
            if match:
                document_to_sources[document_id] = match.group(1).lower()

    active_sources = {
        str(row["source_id"]): str(row["source_path"])
        for row in _active(db)
    }
    return map_community_sources(
        community_to_units=community_to_units,
        unit_to_documents=unit_to_documents,
        document_to_sources=document_to_sources,
        active_sources=active_sources,
        max_sources=8,
    )


def _handle(body: dict) -> dict:
    root = Path(body["data_dir"]).resolve()
    vault = Path(body["vault_root"]).resolve()
    api_base = str(body["api_base"])
    if not _loopback(api_base):
        raise ValueError("GraphRAG model endpoint must use HTTP on a loopback address")
    embedding_model = str(body.get("embedding_model") or "").strip()
    if embedding_model in {"", "local-embedding"}:
        embedding_model = "BAAI/bge-small-en-v1.5"
    if embedding_model != "BAAI/bge-small-en-v1.5":
        raise ValueError("Only the bounded local BAAI/bge-small-en-v1.5 embedding model is enabled")
    body = {**body, "embedding_model": embedding_model}
    root.mkdir(parents=True, exist_ok=True)
    db = _state_db(root)
    try:
        op = str(body.get("op", ""))
        if op == "index_batch":
            raw_paths = body.get("paths")
            if not isinstance(raw_paths, list) or not raw_paths:
                raise ValueError("GraphRAG batch requires one or more source paths")
            if len(raw_paths) > int(body["max_sources"]):
                raise ValueError("GraphRAG batch exceeds configured source limit")
            source_ids: list[str] = []
            for raw_path in raw_paths:
                source_path = Path(str(raw_path)).resolve()
                if not source_path.is_relative_to(vault) or source_path.suffix.lower() not in {
                    ".md",
                    ".txt",
                }:
                    raise ValueError(
                        "Only Markdown/text pages inside the authorized wiki vault can be indexed"
                    )
                if not source_path.is_file():
                    raise ValueError(f"GraphRAG source no longer exists: {source_path}")
                text = source_path.read_text(encoding="utf-8")
                if not text.strip():
                    raise ValueError(f"GraphRAG source is empty: {source_path}")
                if len(text) > int(body["max_source_chars"]):
                    raise ValueError(f"GraphRAG source exceeds configured limit: {source_path}")
                source_id = hashlib.sha256(str(source_path).casefold().encode("utf-8")).hexdigest()
                relative = source_path.relative_to(vault).as_posix()
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                db.execute(
                    """INSERT OR REPLACE INTO sources
                       (source_id, source_path, content, content_hash)
                       VALUES (?, ?, ?, ?)""",
                    (source_id, relative, text, content_hash),
                )
                source_ids.append(source_id)
            db.commit()
            with _local_embedding_api(
                str(body["embedding_model"]), root / "embedding-cache"
            ) as embedding_api_base:
                result = _build_index(root, db, body, embedding_api_base)
            return {"ok": True, **result, "source_ids": source_ids}
        if op in {"index", "forget"}:
            source_path = Path(body["path"]).resolve()
            if not source_path.is_relative_to(vault) or source_path.suffix.lower() not in {
                ".md",
                ".txt",
            }:
                raise ValueError(
                    "Only Markdown/text pages inside the authorized wiki vault can be indexed"
                )
            if op == "index":
                if not source_path.is_file():
                    raise ValueError("GraphRAG source no longer exists")
                text = source_path.read_text(encoding="utf-8")
                if not text.strip():
                    raise ValueError("GraphRAG source is empty")
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                source_id = hashlib.sha256(str(source_path).casefold().encode("utf-8")).hexdigest()
                relative = source_path.relative_to(vault).as_posix()
                db.execute(
                    """INSERT OR REPLACE INTO sources
                       (source_id, source_path, content, content_hash)
                       VALUES (?, ?, ?, ?)""",
                    (source_id, relative, text, content_hash),
                )
                db.commit()
            else:
                source_id = hashlib.sha256(str(source_path).casefold().encode("utf-8")).hexdigest()
                db.execute("DELETE FROM sources WHERE source_id=?", (source_id,))
                db.commit()
            with _local_embedding_api(
                str(body["embedding_model"]), root / "embedding-cache"
            ) as embedding_api_base:
                result = _build_index(root, db, body, embedding_api_base)
            return {"ok": True, **result, "source_id": source_id}
        if op == "search":
            row = db.execute("SELECT value FROM metadata WHERE key='current_project'").fetchone()
            if row is None:
                return {"ok": True, "hits": []}
            with _local_embedding_api(
                str(body["embedding_model"]), root / "embedding-cache"
            ) as embedding_api_base:
                _settings(Path(row["value"]), body, embedding_api_base)
                result = _query(Path(row["value"]), body, db)
            return {"ok": True, **result}
        if op == "global_search":
            from graphrag.cli.query import run_global_search

            row = db.execute("SELECT value FROM metadata WHERE key='current_project'").fetchone()
            if row is None:
                return {"ok": True, "answer": "", "sources": [], "communities": []}
            with _local_embedding_api(
                str(body["embedding_model"]), root / "embedding-cache"
            ) as embedding_api_base:
                _settings(Path(row["value"]), body, embedding_api_base)
                answer, context = run_global_search(
                    data_dir=None,
                    root_dir=Path(row["value"]),
                    community_level=None,
                    dynamic_community_selection=False,
                    response_type=(
                        "Answer in at most 120 words. State only relationships directly supported "
                        "by the reports. Cite each factual claim with its [Data: Reports (...)] "
                        "reference. Do not add implications or outside facts."
                    ),
                    streaming=False,
                    query=str(body["query"]),
                    verbose=False,
                )
            community_rows = context.get("reports") if isinstance(context, dict) else None
            communities = (
                community_rows.to_dict(orient="records") if community_rows is not None else []
            )
            return {
                "ok": True,
                "answer": str(answer),
                "source_scope": "full_indexed_corpus",
                "sources": [
                    {
                        "source_id": source["source_id"],
                        "source_path": source["source_path"],
                    }
                    for source in _active(db)
                ],
                "communities": communities[:20],
            }
        if op == "status":
            count = db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            current = db.execute(
                "SELECT value FROM metadata WHERE key='current_project'"
            ).fetchone()
            return {"ok": True, "source_count": count, "workspace": current[0] if current else None}
        if op == "communities":
            current = db.execute(
                "SELECT value FROM metadata WHERE key='current_project'"
            ).fetchone()
            if current is None:
                return {"ok": True, "communities": [], "community_count": 0}
            project = Path(current[0]).resolve()
            report_path = project / "output" / "community_reports.parquet"
            if not report_path.is_file() or not (project / "index.complete").is_file():
                raise RuntimeError("The current GraphRAG index has no complete community reports")
            import pyarrow.parquet as pq

            parquet = pq.ParquetFile(report_path)
            count = int(parquet.metadata.num_rows)
            limit = max(1, min(int(body.get("limit", 80)), 100))
            columns = [
                "id", "human_readable_id", "community", "level", "title",
                "summary", "rank", "size",
            ]
            communities: list[dict[str, Any]] = []
            for group_index in range(parquet.num_row_groups):
                rows = parquet.read_row_group(group_index, columns=columns).slice(
                    0, limit - len(communities)
                )
                for row in rows.to_pylist():
                    communities.append(
                        {
                            "id": str(row["id"]),
                            "human_readable_id": int(row["human_readable_id"]),
                            "level": int(row["level"]),
                            "title": str(row["title"] or "Untitled community")[:180],
                            "summary": str(row["summary"] or "")[:900],
                            "rank": float(row["rank"] or 0),
                            "size": int(row["size"] or 0),
                        }
                    )
                if len(communities) >= limit:
                    break
            source_links = _community_source_links(project, communities, db)
            for community in communities:
                community["sources"] = source_links.get(int(community["community"]), [])
                community.pop("community", None)
            return {
                "ok": True,
                "communities": communities,
                "community_count": count,
                "workspace_id": project.name,
            }
        raise ValueError(f"Unknown GraphRAG operation: {op}")
    finally:
        db.close()


if __name__ == "__main__":
    try:
        request = json.loads(sys.stdin.read())
        result = _handle(request)
    except Exception as exc:  # one bounded error response for the parent adapter
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, ensure_ascii=False))
