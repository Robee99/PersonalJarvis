"""The project thread: a trunk of branches, each with what was said inside it."""

from __future__ import annotations

from pathlib import Path

from jarvis.memory.brain_map import query, thread
from jarvis.memory.brain_map.indexer import (
    BrainMapIndexer,
    primary_id,
    project_id_for_key,
    project_key_for_path,
)
from jarvis.memory.brain_map.sources import AgentChatReader
from jarvis.memory.brain_map.store import BrainMapStore
from tests.fakes.fake_brain_map import ChatFixture, FakeClock

_SOURCE = {
    "source_kind": "agent_chat",
    "source_id": "s",
    "conversation_id": "s",
    "api": None,
    "locator": "",
}


def _entry(entity_id, kind, via, first, *, source_kind="agent_chat"):
    return {
        "id": entity_id,
        "kind": kind,
        "title": entity_id.title(),
        "status": "blue",
        "status_reason": "",
        "assignment": "confirmed",
        "last_activity_ms": first,
        "source": _SOURCE,
        "via": via,
        "first_seen_ms": first,
        "source_kind": source_kind,
    }


def test_branch_kinds_name_what_each_member_is_on_the_thread() -> None:
    assert thread.branch_kind("CONVERSATION", "agent_chat") == "topic"
    assert thread.branch_kind("THREAD", "ide_thread") == "topic"
    assert thread.branch_kind("TASK", "mission") == "experiment"
    assert thread.branch_kind("TASK", "quest") == "experiment"
    assert thread.branch_kind("TASK", "agent_chat") == "subtask"
    assert thread.branch_kind("OUTPUT", "output") == "artifact"
    assert thread.branch_kind("AGENT", "agent_chat") is None


def test_tree_follows_via_and_orders_by_first_appearance() -> None:
    tree = thread.build_thread(
        "P",
        [
            _entry("chat-b", "CONVERSATION", "P", 200),
            _entry("chat-a", "CONVERSATION", "P", 100),
            _entry("decision", "DECISION", "chat-a", 150),
            _entry("todo", "TASK", "chat-a", 120),
            _entry("run", "TASK", "P", 300, source_kind="mission"),
            _entry("report", "OUTPUT", "run", 310, source_kind="output"),
            _entry("orphan", "TASK", "gone", 50),
            _entry("bot", "AGENT", "P", 10),
        ],
    )
    trunk = [(b["id"], b["branch"]) for b in tree["branches"]]
    assert trunk == [
        ("orphan", "subtask"),
        ("chat-a", "topic"),
        ("chat-b", "topic"),
        ("run", "experiment"),
    ]
    chat_a = tree["branches"][1]
    assert [c["id"] for c in chat_a["children"]] == ["todo", "decision"]
    assert tree["branches"][3]["children"][0]["branch"] == "artifact"
    assert tree["counts"]["topic"] == 2 and tree["counts"]["experiment"] == 1
    assert tree["hidden_branches"] == 0


def test_large_threads_keep_the_newest_and_count_the_rest() -> None:
    entries = [_entry(f"c{i:03d}", "CONVERSATION", "P", i) for i in range(thread.MAX_BRANCHES + 5)]
    entries += [
        _entry(f"d{i:03d}", "DECISION", "c124", 1000 + i) for i in range(thread.MAX_CHILDREN + 3)
    ]
    tree = thread.build_thread("P", entries)
    assert len(tree["branches"]) == thread.MAX_BRANCHES
    assert tree["hidden_branches"] == 5
    assert tree["branches"][0]["id"] == "c005"
    last = tree["branches"][-1]
    assert last["id"] == "c124"
    assert len(last["children"]) == thread.MAX_CHILDREN
    assert last["hidden_children"] == 3


def test_project_detail_serves_the_thread_from_the_indexed_conversations(tmp_path: Path) -> None:
    clock = FakeClock()
    data = tmp_path / "data"
    data.mkdir()
    chat = ChatFixture(data, clock)
    store = BrainMapStore(data / "jarvis.db")
    indexer = BrainMapIndexer(store, [AgentChatReader(data / "agent_chat.db")], clock=clock)
    try:
        sid = chat.session(cwd=str(tmp_path / "orb"))
        chat.say(sid, "We decided to draw the thread as a tree.")
        clock.advance(seconds=30)
        chat.say(sid, "I need to add a breadcrumb back to the trunk.")
        indexer.run(trigger="test")
        detail = query.project_detail(
            store, project_id_for_key(project_key_for_path(str(tmp_path / "orb")))
        )
        assert detail is not None
        (topic,) = detail["thread"]["branches"]
        assert topic["id"] == primary_id("agent_chat", sid)
        assert topic["branch"] == "topic"
        assert [c["branch"] for c in topic["children"]] == ["decision", "subtask"]
        assert topic["source"]["api"] == f"/api/agent-chat/sessions/{sid}"
    finally:
        chat.close()
        store.close()
