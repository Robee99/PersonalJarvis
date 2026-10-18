"""Bounded SQLite vector store plugin for the isolated GraphRAG worker.

GraphRAG exposes custom vector-store registration. This adapter avoids the
native LanceDB manifest replacement path that fails on this Windows host.
It is intentionally optimized for small local corpora, not large ANN indexes.
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from graphrag_vectors.filtering import (
    AndExpr,
    Condition,
    FilterExpr,
    NotExpr,
    Operator,
    OrExpr,
)
from graphrag_vectors.vector_store import (
    VectorStore,
    VectorStoreDocument,
    VectorStoreSearchResult,
)
from graphrag_vectors.vector_store_factory import register_vector_store


def _json_value(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


class JarvisSQLiteVectorStore(VectorStore):
    """SQLite persistence with bounded exact cosine search for local corpora."""

    def __init__(self, db_uri: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.db_uri = str(db_uri)
        self._connection: sqlite3.Connection | None = None
        self._table = "vectors_" + "".join(
            char if char.isalnum() else "_" for char in (self.index_name or "default")
        )

    @property
    def _table_sql(self) -> str:
        # The identifier is derived from GraphRAG's index name and sanitized
        # above to ASCII alphanumerics/underscores before interpolation.
        return f'"{self._table}"'

    def connect(self) -> None:
        path = Path(self.db_uri).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(path), timeout=30)
        self._connection.row_factory = sqlite3.Row

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            self.connect()
        assert self._connection is not None
        return self._connection

    def create_index(self) -> None:
        self._db().execute(
            f'''CREATE TABLE IF NOT EXISTS {self._table_sql} (
                id TEXT PRIMARY KEY,
                vector_json TEXT NOT NULL,
                data_json TEXT NOT NULL,
                create_date TEXT,
                update_date TEXT
            )'''
        )
        self._db().commit()

    def load_documents(self, documents: list[VectorStoreDocument]) -> None:
        rows: list[tuple[str, str, str, str | None, str | None]] = []
        for document in documents:
            self._prepare_document(document)
            if document.vector is None:
                continue
            vector = [float(item) for item in document.vector]
            if self.vector_size and len(vector) != self.vector_size:
                raise ValueError(
                    f"Vector for '{document.id}' has {len(vector)} dimensions; "
                    f"index expects {self.vector_size}"
                )
            rows.append(
                (
                    str(document.id),
                    json.dumps(vector, separators=(",", ":")),
                    json.dumps(document.data or {}, default=_json_value, separators=(",", ":")),
                    document.create_date,
                    document.update_date,
                )
            )
        if rows:
            self._db().executemany(
                f'''INSERT OR REPLACE INTO {self._table_sql}
                    (id, vector_json, data_json, create_date, update_date)
                    VALUES (?, ?, ?, ?, ?)''',  # noqa: S608
                rows,
            )
            self._db().commit()

    @staticmethod
    def _matches(expr: FilterExpr | None, data: dict[str, Any]) -> bool:
        if expr is None:
            return True
        if isinstance(expr, AndExpr):
            return all(JarvisSQLiteVectorStore._matches(item, data) for item in expr.and_)
        if isinstance(expr, OrExpr):
            return any(JarvisSQLiteVectorStore._matches(item, data) for item in expr.or_)
        if isinstance(expr, NotExpr):
            return not JarvisSQLiteVectorStore._matches(expr.not_, data)
        if not isinstance(expr, Condition):
            return False
        actual = data.get(expr.field)
        expected = expr.value
        operator = (
            expr.operator.value
            if isinstance(expr.operator, Operator)
            else str(expr.operator)
        )
        if operator == "exists":
            return (actual is not None) is bool(expected)
        if actual is None:
            return operator == "ne"
        try:
            if operator == "eq":
                return actual == expected
            if operator == "ne":
                return actual != expected
            if operator == "gt":
                return actual > expected
            if operator == "gte":
                return actual >= expected
            if operator == "lt":
                return actual < expected
            if operator == "lte":
                return actual <= expected
            if operator == "in":
                return actual in expected
            if operator == "not_in":
                return actual not in expected
            if operator == "contains":
                return str(expected) in str(actual)
            if operator == "startswith":
                return str(actual).startswith(str(expected))
            if operator == "endswith":
                return str(actual).endswith(str(expected))
        except (TypeError, ValueError):
            return False
        return False

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if len(left) != len(right):
            return -1.0
        left_norm = math.sqrt(sum(item * item for item in left))
        right_norm = math.sqrt(sum(item * item for item in right))
        if not left_norm or not right_norm:
            return 0.0
        return sum(a * b for a, b in zip(left, right, strict=True)) / (
            left_norm * right_norm
        )

    def similarity_search_by_vector(
        self,
        query_embedding: list[float],
        k: int = 10,
        select: list[str] | None = None,
        filters: FilterExpr | None = None,
        include_vectors: bool = True,
    ) -> list[VectorStoreSearchResult]:
        rows = self._db().execute(
            f'''SELECT id, vector_json, data_json, create_date, update_date
                FROM {self._table_sql}'''  # noqa: S608
        ).fetchall()
        results: list[VectorStoreSearchResult] = []
        for row in rows:
            data = json.loads(row["data_json"])
            if not self._matches(filters, data):
                continue
            vector = json.loads(row["vector_json"])
            selected = select if select is not None else list(data)
            result_data = {key: data[key] for key in selected if key in data}
            results.append(
                VectorStoreSearchResult(
                    document=VectorStoreDocument(
                        id=row["id"],
                        vector=vector if include_vectors else None,
                        data=result_data,
                        create_date=row["create_date"],
                        update_date=row["update_date"],
                    ),
                    score=self._cosine(query_embedding, vector),
                )
            )
        results.sort(key=lambda item: item.score, reverse=True)
        return results[: max(1, int(k))]

    def search_by_id(
        self,
        document_id: str,
        select: list[str] | None = None,
        include_vectors: bool = True,
    ) -> VectorStoreDocument:
        row = self._db().execute(
            f'''SELECT id, vector_json, data_json, create_date, update_date
                FROM {self._table_sql} WHERE id=?''',  # noqa: S608
            (str(document_id),),
        ).fetchone()
        if row is None:
            raise IndexError(f"Document with id '{document_id}' not found")
        data = json.loads(row["data_json"])
        selected = select if select is not None else list(data)
        return VectorStoreDocument(
            id=row["id"],
            vector=json.loads(row["vector_json"]) if include_vectors else None,
            data={key: data[key] for key in selected if key in data},
            create_date=row["create_date"],
            update_date=row["update_date"],
        )

    def count(self) -> int:
        row = self._db().execute(
            f'''SELECT COUNT(*) AS n FROM {self._table_sql}'''  # noqa: S608
        ).fetchone()
        return int(row["n"])

    def remove(self, ids: list[str]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        self._db().execute(
            f'''DELETE FROM {self._table_sql} WHERE id IN ({placeholders})''',  # noqa: S608
            [str(item) for item in ids],
        )
        self._db().commit()

    def update(self, document: VectorStoreDocument) -> None:
        current = self.search_by_id(str(document.id))
        self._prepare_update(document)
        data = current.data | (document.data or {})
        vector = document.vector if document.vector is not None else current.vector
        self._db().execute(
            f'''UPDATE {self._table_sql}
                SET vector_json=?, data_json=?, update_date=? WHERE id=?''',  # noqa: S608
            (
                json.dumps(vector or [], separators=(",", ":")),
                json.dumps(data, default=_json_value, separators=(",", ":")),
                document.update_date,
                str(document.id),
            ),
        )
        self._db().commit()


def register_jarvis_sqlite_vector_store() -> None:
    """Register the isolated local backend through GraphRAG's public extension point."""
    register_vector_store("jarvis_sqlite", JarvisSQLiteVectorStore)
