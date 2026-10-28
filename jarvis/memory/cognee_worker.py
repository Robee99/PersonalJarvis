"""One-request Cognee worker; run only with the configured isolated Python.

Protocol: one JSON object on stdin and one JSON object on stdout. Cognee's own
logs are redirected to stderr. This worker owns no network listener.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any

NAMESPACE = uuid.UUID("6ac9a1ea-8a4e-5571-8f9b-7e2aa8dad861")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS sources (
        source_id TEXT PRIMARY KEY, source_path TEXT NOT NULL, data_id TEXT NOT NULL,
        content_hash TEXT NOT NULL, status TEXT NOT NULL,
        source_type TEXT NOT NULL DEFAULT 'wiki', source_ref TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sources)")}
    if "source_type" not in columns:
        conn.execute("ALTER TABLE sources ADD COLUMN source_type TEXT NOT NULL DEFAULT 'wiki'")
    if "source_ref" not in columns:
        conn.execute("ALTER TABLE sources ADD COLUMN source_ref TEXT NOT NULL DEFAULT ''")
    conn.commit()


def _source_row(conn: sqlite3.Connection, source_id: str):
    return conn.execute(
        "SELECT * FROM sources WHERE source_id=?",
        (source_id,),
    ).fetchone()


async def _run(request: dict[str, Any]) -> dict[str, Any]:
    # Import only after the isolated data/system roots have been set by parent.
    import cognee

    # Cognee's gliner_demo default is a ~750 MB base checkpoint. Keep this
    # isolated worker bounded for desktop CPU use by selecting Fastino's
    # Apache-2.0 74M small checkpoint and a small inference batch.
    import cognee.tasks.graph.gliner_demo.tasks as gliner_tasks
    from cognee.api.v1.search import SearchType
    from cognee.tasks.ingestion.data_item import DataItem

    upstream_gliner_task_factory = gliner_tasks.get_gliner_demo_tasks

    async def bounded_gliner_tasks(*args: Any, **kwargs: Any):
        kwargs.update(
            {
                "model_name": "fastino/gliner2.5-small-v1",
                "gliner_batch_size": 2,
                "window_words": 256,
                "window_overlap_words": 32,
            }
        )
        return await upstream_gliner_task_factory(*args, **kwargs)

    gliner_tasks.get_gliner_demo_tasks = bounded_gliner_tasks

    dataset = str(request["dataset"])
    vault = await asyncio.to_thread(Path(request["vault_root"]).resolve)
    state_path = Path(request["state_db"])
    await asyncio.to_thread(state_path.parent.mkdir, parents=True, exist_ok=True)
    conn = sqlite3.connect(state_path)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)

    async def forget(row: sqlite3.Row) -> None:
        await cognee.forget(data_id=uuid.UUID(row["data_id"]), dataset=dataset)

    async def index(
        source_id: str,
        source_path: str,
        content: str,
        source_type: str = "wiki",
        source_ref: str = "",
    ) -> dict[str, Any]:
        rel = Path(source_path)
        if source_type == "wiki":
            if rel.is_absolute() or ".." in rel.parts or rel.suffix.lower() != ".md":
                raise ValueError("source must be a vault-relative Markdown path")
            full = await asyncio.to_thread((vault / rel).resolve)
            if not full.is_relative_to(vault):
                raise ValueError("source is outside the authorized wiki vault")
        elif source_type == "conversation":
            if (
                rel.is_absolute()
                or ".." in rel.parts
                or rel.parts[0:1] != ("conversations",)
                or rel.suffix.lower() != ".md"
                or not source_ref
            ):
                raise ValueError("conversation source identity is invalid")
        else:
            raise ValueError("unsupported Cognee source type")
        content_hash = _hash(content)
        previous = _source_row(conn, source_id)
        if (
            previous and previous["status"] == "active"
            and previous["content_hash"] == content_hash
            and previous["source_type"] == source_type
            and previous["source_ref"] == source_ref
        ):
            # Idempotency is verified against Cognee search, not just a cache hit.
            return {
                "indexed": False,
                "unchanged": True,
                "source_id": source_id,
                "source_path": source_path,
            }
        # Keep source_id stable for citations while assigning an immutable
        # Cognee document id to each content version. Cognee's forget() can
        # leave old derived chunk rows behind on some stores; versioned ids
        # make those rows fail the active-source allowlist after an update.
        data_id = str(uuid.uuid5(NAMESPACE, f"{source_id}:{content_hash}"))
        # Persist a tombstone before destructive replacement. A crash or failed
        # forget can never cause search to authorize the old derived record.
        conn.execute(
            """INSERT INTO sources(
                source_id,source_path,data_id,content_hash,status,source_type,source_ref)
            VALUES(?,?,?,?, 'tombstone',?,?) ON CONFLICT(source_id) DO UPDATE SET
            source_path=excluded.source_path,data_id=excluded.data_id,
            content_hash=excluded.content_hash,status='tombstone',
            source_type=excluded.source_type,source_ref=excluded.source_ref,
            updated_at=CURRENT_TIMESTAMP""",
            (source_id, source_path, data_id, content_hash, source_type, source_ref),
        )
        conn.commit()
        if previous:
            try:
                await forget(previous)
            except Exception as exc:
                # A missing item is harmless; persistent tombstone is the
                # retrieval authorization boundary if Cognee's delete fails.
                logging.getLogger(__name__).warning(
                    "Cognee cleanup failed (%s); source remains tombstoned",
                    type(exc).__name__,
                )
        item = DataItem(
            data=content,
            label=source_path,
            external_metadata={
                "source_id": source_id,
                "source_path": source_path,
                "content_hash": content_hash,
                "source_system": "jarvis-wiki" if source_type == "wiki" else "jarvis-conversation",
                "source_type": source_type,
                "source_ref": source_ref,
            },
            data_id=uuid.UUID(data_id),
            literal_text=True,
        )
        await cognee.add(item, dataset_name=dataset)
        # This is Cognee's local GLiNER extractor; provider credentials are
        # stripped by Jarvis and the extractor has no hosted LLM dependency.
        # The isolated CPU pipeline has no hosted LLM credentials by design.
        # Derive summaries from local GLiNER extraction rather than allowing
        # Cognee's default summary mode to select an LLM provider.
        await cognee.cognify(
            datasets=[dataset],
            extractor="gliner_demo",
            summary_method="from_extraction",
        )
        conn.execute(
            "UPDATE sources SET status='active',updated_at=CURRENT_TIMESTAMP WHERE source_id=?",
            (source_id,),
        )
        conn.commit()
        return {
            "indexed": True,
            "unchanged": False,
            "source_id": source_id,
            "source_path": source_path,
            "source_type": source_type,
            "source_ref": source_ref,
            "content_hash": content_hash,
            "data_id": data_id,
        }

    async def reconcile(denied_source_ids: set[str]) -> None:
        rows = conn.execute(
            "SELECT * FROM sources WHERE status='active' AND source_type='wiki'"
        ).fetchall()
        for row in rows:
            source = await asyncio.to_thread((vault / row["source_path"]).resolve)
            source_exists = await asyncio.to_thread(source.is_file)
            if (
                row["source_id"] in denied_source_ids
                or not source.is_relative_to(vault)
                or not source_exists
            ):
                conn.execute(
                    "UPDATE sources SET status='tombstone',updated_at=CURRENT_TIMESTAMP "
                    "WHERE source_id=?",
                    (row["source_id"],),
                )
                conn.commit()
                try:
                    await forget(row)
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "Cognee cleanup failed (%s); source remains tombstoned",
                        type(exc).__name__,
                    )
                continue
            body = await asyncio.to_thread(source.read_text, encoding="utf-8", errors="replace")
            if _hash(body) != row["content_hash"]:
                await index(row["source_id"], row["source_path"], body)

    op = request.get("op")
    try:
        if op == "index":
            result = await index(
                str(request["source_id"]),
                str(request["source_path"]),
                str(request["content"]),
                str(request.get("source_type", "wiki")),
                str(request.get("source_ref", "")),
            )
        elif op == "forget":
            source_id = str(request["source_id"])
            row = _source_row(conn, source_id)
            if row:
                conn.execute(
                    "UPDATE sources SET status='tombstone',updated_at=CURRENT_TIMESTAMP "
                    "WHERE source_id=?",
                    (source_id,),
                )
                conn.commit()
                await forget(row)
            result = {
                "forgotten": True,
                "source_id": source_id,
                "source_path": str(request["source_path"]),
                "source_type": str(request.get("source_type", "wiki")),
            }
        elif op == "search":
            source_types = {
                str(value) for value in request.get("source_types", ["wiki"])
            }
            # Conversation retrieval must not read or reindex wiki files: only
            # the wiki adapter supplies the source-content safety denylist.
            if "wiki" in source_types:
                await reconcile(
                    {str(source_id) for source_id in request.get("deny_source_ids", [])}
                )
            results = await cognee.search(
                str(request["query"]),
                query_type=SearchType.CHUNKS,
                datasets=[dataset],
                include_references=True,
                top_k=int(request.get("k", 5)),
            )
            active = {
                row["data_id"]: row
                for row in conn.execute("SELECT * FROM sources WHERE status='active'").fetchall()
                if row["source_type"] in source_types
            }
            output = []
            # CHUNKS is a non-generative Cognee retriever: the public search
            # API flattens each SearchResultPayload to a list of chunk payload
            # dicts (not evidence-bearing SearchResultPayload objects). Older
            # Cognee versions may instead return DTOs, so accept both shapes.
            candidates: list[Any] = []
            for item in results:
                raw = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                if isinstance(raw, dict) and "search_result" in raw:
                    raw = raw["search_result"]
                candidates.extend(raw if isinstance(raw, list) else [raw])
            for raw in candidates:
                if not isinstance(raw, dict):
                    continue
                refs = raw.get("evidence", raw.get("references", []))
                if not refs and isinstance(raw.get("reference"), dict):
                    refs = [raw["reference"]]
                source_ids = [str(raw.get("source_id", ""))]
                metadata = raw.get("external_metadata")
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except json.JSONDecodeError:  # Bad optional metadata contributes no source identifiers.
                        metadata = {}
                if isinstance(metadata, dict):
                    source_ids.append(str(metadata.get("source_id", "")))
                source_ids.extend(
                    str(ref.get("data_id", ref.get("dataId", "")))
                    for ref in refs
                    if isinstance(ref, dict)
                )
                source_ids.append(str(raw.get("document_id", "")))
                matched = next((active[key] for key in source_ids if key in active), None)
                if matched:
                    text = (
                        raw.get("context")
                        or raw.get("result_object")
                        or raw.get("text")
                        or raw.get("content", "")
                    )
                    if isinstance(text, list):
                        text = "\n".join(str(part) for part in text)
                    output.append(
                        {
                            "text": str(text),
                            "source_id": matched["source_id"],
                            "source_path": matched["source_path"],
                            "source_type": matched["source_type"],
                            "source_ref": matched["source_ref"],
                            "content_hash": matched["content_hash"],
                            "data_id": matched["data_id"],
                        }
                    )
            result = {"results": output}
        elif op == "health":
            result = {
                "version": cognee.__version__,
                "active_sources": conn.execute(
                    "SELECT count(*) FROM sources WHERE status='active'"
                ).fetchone()[0],
            }
        else:
            raise ValueError("unsupported operation")
        return {"ok": True, **result}
    finally:
        conn.close()


def main() -> int:
    try:
        request = json.loads(sys.stdin.readline())
        # Keep third-party logging off protocol stdout.
        with contextlib.redirect_stdout(sys.stderr):
            result = asyncio.run(_run(request))
        sys.stdout.write(json.dumps({"ok": True, **result}, ensure_ascii=False) + "\n")
        return 0
    except Exception as exc:  # protocol boundary reports failure to caller
        sys.stdout.write(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}) + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
