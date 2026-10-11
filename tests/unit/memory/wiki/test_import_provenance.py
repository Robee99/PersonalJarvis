"""Imported chat exports keep their conversation ids, message ids and timestamps."""

from __future__ import annotations

import json
from pathlib import Path

from jarvis.memory.wiki.import_formats import (
    conversation_of,
    conversation_pages,
    parse_conversation_markers,
)

CHATGPT = {
    "title": "Falcon launch plan",
    "id": "6650f2c1-aaaa-bbbb-cccc-0123456789ab",
    "create_time": 1_790_000_000.5,
    "current_node": "n2",
    "mapping": {
        "n1": {
            "id": "n1",
            "parent": None,
            "message": {
                "id": "msg-1",
                "create_time": 1_790_000_000.5,
                "author": {"role": "user"},
                "content": {"parts": ["When is launch?"]},
            },
        },
        "n2": {
            "id": "n2",
            "parent": "n1",
            "message": {
                "id": "msg-2",
                "create_time": 1_790_000_030,
                "author": {"role": "assistant"},
                "content": {"parts": ["In March."]},
            },
        },
    },
}

CLAUDE = {
    "uuid": "c0ffee00-1111-2222-3333-444455556666",
    "name": "Garden ideas",
    "created_at": "2026-09-01T10:00:00Z",
    "chat_messages": [
        {
            "uuid": "m-a",
            "sender": "human",
            "text": "Tomatoes?",
            "created_at": "2026-09-01T10:00:00Z",
        },
        {
            "uuid": "m-b",
            "sender": "assistant",
            "text": "Yes.",
            "created_at": "2026-09-01T10:00:05.250Z",
        },
    ],
}


def test_chatgpt_export_keeps_ids_and_times() -> None:
    conv = conversation_of(CHATGPT)
    assert conv is not None
    assert conv.conversation_id == CHATGPT["id"]
    assert conv.created_ms == 1_790_000_000_500
    assert conv.source_format == "chatgpt"
    assert [(t.message_id, t.ts_ms) for t in conv.turns] == [
        ("msg-1", 1_790_000_000_500),
        ("msg-2", 1_790_000_030_000),
    ]


def test_claude_export_keeps_ids_and_iso_times() -> None:
    conv = conversation_of(CLAUDE)
    assert conv is not None
    assert conv.conversation_id == CLAUDE["uuid"]
    assert conv.turns[1].message_id == "m-b"
    assert conv.turns[1].ts_ms == 1_788_256_805_250


def test_dataset_without_ids_gets_a_stable_derived_id() -> None:
    record = {
        "messages": [
            {"role": "user", "content": "hi there"},
            {"role": "assistant", "content": "hello"},
        ]
    }
    first, second = conversation_of(record), conversation_of(dict(record))
    assert first is not None and second is not None
    assert first.conversation_id.startswith("derived-")
    assert first.conversation_id == second.conversation_id


def test_rendered_page_round_trips_the_provenance(tmp_path: Path) -> None:
    raw = json.dumps([CHATGPT, CLAUDE]).encode()
    pages = conversation_pages(tmp_path / "conversations.json", raw)
    assert pages is not None and len(pages) == 2
    falcon = pages[0].body
    assert "**You:**\n\nWhen is launch?" in falcon  # the reading view is unchanged
    turns = parse_conversation_markers(falcon)
    assert [(t.conversation_id, t.message_id, t.ts_ms, t.role, t.text) for t in turns] == [
        (CHATGPT["id"], "msg-1", 1_790_000_000_500, "You", "When is launch?"),
        (CHATGPT["id"], "msg-2", 1_790_000_030_000, "Assistant", "In March."),
    ]
    assert turns[0].title == "Falcon launch plan"


def test_dataset_page_keeps_each_conversation_apart(tmp_path: Path) -> None:
    lines = [
        json.dumps({"id": f"c{i}", "messages": [{"role": "user", "content": f"question {i}"}]})
        for i in range(3)
    ]
    pages = conversation_pages(tmp_path / "set.jsonl", "\n".join(lines).encode())
    assert pages is not None and len(pages) == 1
    turns = parse_conversation_markers(pages[0].body)
    assert [(t.conversation_id, t.text) for t in turns] == [
        ("c0", "question 0"),
        ("c1", "question 1"),
        ("c2", "question 2"),
    ]


def test_marker_values_cannot_break_out_of_the_comment() -> None:
    hostile = dict(CHATGPT, id='x" --><script>alert(1)</script>')
    conv = conversation_of(hostile)
    assert conv is not None
    assert conv.conversation_id == "x--scriptalert1script"
    assert ">" not in conv.conversation_id and '"' not in conv.conversation_id
