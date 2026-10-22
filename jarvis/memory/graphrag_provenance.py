"""Map GraphRAG communities back to the active source documents they contain."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def map_community_sources(
    *,
    community_to_units: Mapping[int, Iterable[str]],
    unit_to_documents: Mapping[str, str],
    document_to_sources: Mapping[str, str],
    active_sources: Mapping[str, str],
    max_sources: int = 8,
) -> dict[int, list[dict[str, str]]]:
    """Return bounded provenance links, excluding sources no longer authorized.

    GraphRAG's exported community, text-unit, and document tables form an
    explicit ID chain. Only source IDs in JARVIS's active-source registry may
    leave this function, so a stale export cannot expose a removed wiki page.
    """
    limit = max(0, min(int(max_sources), 32))
    result: dict[int, list[dict[str, str]]] = {}
    if limit == 0:
        return result

    for community_id, unit_ids in community_to_units.items():
        sources: list[dict[str, str]] = []
        seen: set[str] = set()
        for unit_id in sorted({str(value) for value in unit_ids}):
            document_id = unit_to_documents.get(unit_id)
            if document_id is None:
                continue
            source_id = document_to_sources.get(str(document_id))
            if source_id is None or source_id in seen:
                continue
            source_path = active_sources.get(source_id)
            if source_path is None:
                continue
            seen.add(source_id)
            sources.append({"source_id": source_id, "source_path": source_path})
            if len(sources) >= limit:
                break
        if sources:
            result[int(community_id)] = sources
    return result
