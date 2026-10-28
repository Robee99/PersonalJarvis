from jarvis.memory.graphrag_provenance import map_community_sources


def test_community_sources_follow_indexed_units_to_active_authorized_sources() -> None:
    result = map_community_sources(
        community_to_units={7: ["unit-a", "unit-b", "unit-removed"]},
        unit_to_documents={"unit-a": "doc-a", "unit-b": "doc-b", "unit-removed": "doc-x"},
        document_to_sources={"doc-a": "source-a", "doc-b": "source-b", "doc-x": "source-x"},
        active_sources={"source-a": "people/alice.md", "source-b": "projects/kestrel.md"},
    )

    assert result == {
        7: [
            {"source_id": "source-a", "source_path": "people/alice.md"},
            {"source_id": "source-b", "source_path": "projects/kestrel.md"},
        ]
    }


def test_community_sources_are_deduplicated_and_bounded() -> None:
    result = map_community_sources(
        community_to_units={1: ["u1", "u2", "u3"]},
        unit_to_documents={"u1": "d1", "u2": "d1", "u3": "d2"},
        document_to_sources={"d1": "s1", "d2": "s2"},
        active_sources={"s1": "one.md", "s2": "two.md"},
        max_sources=1,
    )

    assert result == {1: [{"source_id": "s1", "source_path": "one.md"}]}
