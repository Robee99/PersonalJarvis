"""The brain map indexer end to end, over real SQLite stores in a tmp dir."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jarvis.memory.brain_map import query
from jarvis.memory.brain_map.indexer import (
    BrainMapIndexer,
    primary_id,
    project_id_for_key,
    project_key_for_path,
)
from jarvis.memory.brain_map.sources import (
    AgentChatReader,
    IdeLibraryReader,
    MissionReader,
    WikiImportReader,
)
from jarvis.memory.brain_map.store import BrainMapStore
from jarvis.memory.wiki.import_formats import conversation_pages
from tests.fakes.fake_brain_map import (
    ChatFixture,
    FakeClock,
    FakeIdeLibrary,
    FakeIdeProject,
    FakeIdeThread,
    missions_db,
    put_mission,
)


@pytest.fixture
def env(tmp_path: Path):
    clock = FakeClock()
    data = tmp_path / "data"
    data.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    chat = ChatFixture(data, clock)
    missions = missions_db(data / "missions.db")
    library = FakeIdeLibrary()
    store = BrainMapStore(data / "jarvis.db")
    readers = [
        IdeLibraryReader(library.list_projects, library.list_threads, library.branch_of),
        AgentChatReader(data / "agent_chat.db"),
        MissionReader(data / "missions.db"),
        WikiImportReader(vault),
    ]
    indexer = BrainMapIndexer(store, readers, clock=clock)

    class Env:
        pass

    e = Env()
    e.clock, e.chat, e.missions, e.library, e.store, e.indexer = (
        clock,
        chat,
        missions,
        library,
        store,
        indexer,
    )
    e.data, e.vault, e.tmp = data, vault, tmp_path
    yield e
    chat.close()
    missions.close()
    store.close()


def _entity(store: BrainMapStore, entity_id: str) -> sqlite3.Row:
    with store.read() as conn:
        row = BrainMapStore.get_entity(conn, entity_id)
    assert row is not None, entity_id
    return row


def _by_kind(store: BrainMapStore, kind: str) -> list[sqlite3.Row]:
    with store.read() as conn:
        return list(conn.execute("SELECT * FROM brain_map_entities WHERE kind=?", (kind,)))


def test_incremental_run_processes_only_new_or_changed_segments(env) -> None:
    sid = env.chat.session(cwd=str(env.tmp / "proj"))
    env.chat.say(sid, "Hello there, let us plan the release.")
    env.chat.say(sid, "Sure. What is first?", role="assistant")

    first = env.indexer.run(trigger="test")
    assert (first.segments_seen, first.segments_processed) == (2, 2)

    second = env.indexer.run(trigger="test")
    assert (second.segments_seen, second.segments_processed) == (0, 0)
    assert second.sources_changed == 0

    env.clock.advance(seconds=60)
    env.chat.say(sid, "TODO: write the changelog.")
    third = env.indexer.run(trigger="test")
    assert (third.segments_seen, third.segments_processed) == (1, 1)
    assert [t["title"] for t in _by_kind(env.store, "TASK")] == ["Write the changelog"]


def test_unchanged_content_hash_is_skipped_even_when_reread(env) -> None:
    sid = env.chat.session(cwd=str(env.tmp / "proj"))
    env.chat.say(sid, "We decided to use Qwen for the local route.")
    env.indexer.run(trigger="test")
    # Force a full re-read of the conversation: the cursor goes back to zero.
    with env.store.write() as conn:
        conn.execute("UPDATE brain_map_sources SET cursor='0', last_activity_ms=0")
    rerun = env.indexer.run(trigger="test")
    assert rerun.segments_seen == 1
    assert rerun.segments_processed == 0


def test_blocked_on_api_key_is_a_red_blocker_with_verbatim_evidence(env) -> None:
    project_path = str(env.tmp / "orb-project")
    sid = env.chat.session(cwd=project_path)
    seq = env.chat.say(sid, "Status update: we're blocked on the API key.")
    env.indexer.run(trigger="test")

    blockers = _by_kind(env.store, "BLOCKER")
    assert len(blockers) == 1
    blocker = blockers[0]
    assert blocker["status"] == "red"
    assert blocker["status_reason"] == "open blocker"

    detail = query.entity_detail(env.store, blocker["entity_id"])
    assert detail is not None
    (evidence,) = detail["evidence"]
    assert evidence["quote"] == "Status update: we're blocked on the API key."
    text = "Status update: we're blocked on the API key."
    assert text[evidence["char_start"] : evidence["char_end"]] == evidence["quote"]
    assert evidence["source"]["source_kind"] == "agent_chat"
    assert evidence["source"]["conversation_id"] == sid
    assert evidence["source"]["message_ref"] == f"seq:{seq}"
    assert evidence["source"]["ts_ms"] == env.clock.ms
    assert evidence["source"]["api"] == f"/api/agent-chat/sessions/{sid}"

    project_id = project_id_for_key(project_key_for_path(project_path))
    assert detail["projects"] == [project_id]
    project = query.project_detail(env.store, project_id)
    assert project is not None
    assert project["status"] == "red"
    assert "blocked on the API key" in project["status_reason"]
    assert [b["id"] for b in project["blockers"]] == [blocker["entity_id"]]
    assert project["next_actions"][0]["action"].startswith("Resolve blocker:")


def test_unblocked_later_resolves_the_blocker_green(env) -> None:
    sid = env.chat.session(cwd=str(env.tmp / "proj"))
    env.chat.say(sid, "We're blocked on the API key.")
    env.indexer.run(trigger="test")
    env.clock.advance(days=1)
    env.chat.say(sid, "Good news: we are no longer blocked, the API key arrived.")
    env.indexer.run(trigger="test")
    (blocker,) = _by_kind(env.store, "BLOCKER")
    assert blocker["status"] == "green"
    assert blocker["status_reason"].startswith("resolved on 2026-10-02")
    history = query.entity_detail(env.store, blocker["entity_id"])["status_history"]
    assert [h["status"] for h in history] == ["red", "green"]


def test_we_decided_to_use_qwen_is_a_decision(env) -> None:
    sid = env.chat.session(cwd=str(env.tmp / "proj"))
    env.chat.say(sid, "After the benchmark we decided to use Qwen.")
    env.indexer.run(trigger="test")
    (decision,) = _by_kind(env.store, "DECISION")
    assert decision["title"] == "After the benchmark we decided to use Qwen"
    assert decision["status"] == "green"
    detail = query.entity_detail(env.store, decision["entity_id"])
    assert detail["relations"]["outgoing"][0]["kind"] == "decided_in"
    assert detail["relations"]["outgoing"][0]["target"] == primary_id("agent_chat", sid)


@pytest.mark.parametrize(
    ("state", "age_days", "status", "reason"),
    [
        ("FAILED", 0, "red", "mission failed"),
        ("TIMED_OUT", 0, "red", "mission timed out"),
        ("APPROVED", 0, "green", "mission approved"),
        ("RUNNING", 0, "blue", "mission running"),
        ("RUNNING", 10, "yellow", "mission running; stale since 2026-09-21"),
    ],
)
def test_mission_states_map_to_colours(env, state, age_days, status, reason) -> None:
    updated = env.clock.ms - int(age_days * 24 * 3600 * 1000)
    put_mission(env.missions, "m-1", "Refactor the router", state, updated)
    env.indexer.run(trigger="test")
    row = _entity(env.store, primary_id("mission", "m-1"))
    assert (row["status"], row["status_reason"]) == (status, reason)


def test_running_mission_turns_yellow_when_it_goes_stale(env) -> None:
    put_mission(env.missions, "m-2", "Write docs", "RUNNING", env.clock.ms)
    env.indexer.run(trigger="test")
    assert _entity(env.store, primary_id("mission", "m-2"))["status"] == "blue"
    env.clock.advance(days=8)
    env.indexer.run(trigger="tick")
    row = _entity(env.store, primary_id("mission", "m-2"))
    assert row["status"] == "yellow"
    assert row["status_reason"] == "mission running; stale since 2026-10-01"


def test_task_said_in_chat_goes_stale_with_a_reason_and_milestone_closes_it(env) -> None:
    sid = env.chat.session(cwd=str(env.tmp / "proj"))
    env.chat.say(sid, "I need to update the release notes for the orb.")
    env.indexer.run(trigger="test")
    (task,) = _by_kind(env.store, "TASK")
    assert task["status"] == "blue"

    env.clock.advance(days=9)
    stats = env.indexer.run(trigger="tick")
    assert stats.segments_processed == 0  # only the time-based rule re-ran
    (task,) = _by_kind(env.store, "TASK")
    assert (task["status"], task["status_reason"]) == (
        "yellow",
        "open task; stale since 2026-10-01",
    )

    env.chat.say(sid, "Done with the release notes for the orb.")
    env.indexer.run(trigger="test")
    (task,) = _by_kind(env.store, "TASK")
    assert task["status"] == "green"
    assert (_by_kind(env.store, "MILESTONE")[0]["status"]) == "green"


def test_priority_orders_projects_with_reasons(env) -> None:
    done = env.chat.session(cwd=str(env.tmp / "gamma-tool"))
    env.chat.say(done, "We decided to ship it as is.")
    env.clock.advance(days=8)  # gamma's chat has been quiet for a week
    blocked = env.chat.session(cwd=str(env.tmp / "alpha-service"))
    env.chat.say(blocked, "We're blocked on the API key, this is urgent.")
    active = env.chat.session(cwd=str(env.tmp / "beta-site"))
    env.chat.say(active, "Next step is the landing page copy.")
    env.indexer.run(trigger="test")

    projects = query.list_projects(env.store)
    titles = [p["title"] for p in projects]
    assert titles == ["alpha-service", "beta-site", "gamma-tool"]
    alpha, beta, gamma = projects
    assert alpha["status"] == "red" and alpha["priority_score"] > beta["priority_score"]
    assert "1 open blocker" in alpha["priority_reason"]
    assert beta["status"] == "blue"
    assert beta["priority_reason"].startswith("Blue: active")
    assert gamma["status"] == "green" and gamma["priority_score"] == 0.0
    assert gamma["priority_reason"].startswith("Nothing to do")

    alpha_detail = query.project_detail(env.store, alpha["id"])
    (blocker,) = alpha_detail["blockers"]
    assert "marked urgent" in blocker["priority_reason"]


def test_ide_project_branch_mention_clusters_a_chat_without_cwd(env) -> None:
    path = str(env.tmp / "brainmap-repo")
    env.library.projects.append(
        FakeIdeProject("p1", path, "brainmap-repo", last_opened_at=env.clock.now)
    )
    env.library.threads["p1"] = [FakeIdeThread("t1", "Wire the graph", updated_at=env.clock.now)]
    env.library.branches[path] = "feat/orb-graph"
    sid = env.chat.session()  # a front-page chat: no folder
    env.chat.say(sid, "I pushed feat/orb-graph, please review it.")
    weak = env.chat.session()
    env.chat.say(weak, "Thinking about brainmap repo layout.")
    env.indexer.run(trigger="test")

    project_id = project_id_for_key(project_key_for_path(path))
    detail = query.project_detail(env.store, project_id)
    assert detail["branches"] == []  # no real .git in tmp; branch_of was the fake
    conv = {c["id"]: c for c in detail["conversations"]}
    assert conv[primary_id("ide_thread", "t1")]["assignment"] == "confirmed"
    assert conv[primary_id("agent_chat", sid)]["assignment"] == "confirmed"
    assert conv[primary_id("agent_chat", sid)]["confidence"] >= 0.6
    assert conv[primary_id("agent_chat", weak)]["assignment"] == "suggested"
    assert detail["counts"]["suggested"] == 1


def test_deleted_conversation_takes_its_derived_entities_along(env) -> None:
    sid = env.chat.session(cwd=str(env.tmp / "proj"))
    env.chat.say(sid, "We're blocked on the design review.")
    env.indexer.run(trigger="test")
    assert _by_kind(env.store, "BLOCKER")
    env.chat.store.delete_session(sid)
    env.indexer.run(trigger="test")
    assert _by_kind(env.store, "BLOCKER") == []
    assert _by_kind(env.store, "CONVERSATION") == []
    with env.store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM brain_map_segments").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM brain_map_evidence").fetchone()[0] == 0


_EXPORT = (
    '[{"title": "Launch plan", "id": "conv-42", "create_time": 1790000000,'
    ' "current_node": "b", "mapping": {'
    '"a": {"id": "a", "parent": null, "message": {"id": "msg-a", "create_time": 1790000000,'
    ' "author": {"role": "user"}, "content": {"parts": ["We decided to launch in March."]}}},'
    '"b": {"id": "b", "parent": "a", "message": {"id": "msg-b", "create_time": 1790000060,'
    ' "author": {"role": "assistant"}, "content": {"parts": ["Great, noted."]}}}}}]'
)


def _write_import(vault: Path, raw: str) -> Path:
    pages = conversation_pages(Path("conversations.json"), raw.encode())
    assert pages
    target = vault / "imports" / "chatgpt-abc123" / "conversations" / f"{pages[0].name}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(pages[0].body, encoding="utf-8")
    return target


def test_imported_conversation_is_a_source_with_ids_and_timestamps(env) -> None:
    page = _write_import(env.vault, _EXPORT)
    env.indexer.run(trigger="test")
    (decision,) = _by_kind(env.store, "DECISION")
    (evidence,) = query.entity_detail(env.store, decision["entity_id"])["evidence"]
    assert evidence["source"]["source_kind"] == "wiki_import"
    assert evidence["source"]["conversation_id"] == "conv-42"
    assert evidence["source"]["message_id"] == "msg-a"
    assert evidence["source"]["ts_ms"] == 1_790_000_000_000

    # Edit only the assistant turn: one segment re-processed, the other skipped.
    env.clock.advance(seconds=5)
    page.write_text(page.read_text().replace("Great, noted.", "Great, noted twice."), "utf-8")
    stats = env.indexer.run(trigger="test")
    assert (stats.segments_seen, stats.segments_processed) == (2, 1)


def test_imported_chats_land_in_one_project_including_pre_marker_pages(env) -> None:
    _write_import(env.vault, _EXPORT)
    legacy = env.vault / "imports" / "ai-data-d81b09" / "conversations-023" / "Old chat.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        "# Old chat\n\n_Conversation imported from `conversations-023.json`,"
        " started 2025-10-19 09:26 UTC_\n\n**You:**\n\nhello\n\n**ChatGPT:**\n\nhi there\n",
        encoding="utf-8",
    )
    env.indexer.run(trigger="test")
    (project,) = _by_kind(env.store, "PROJECT")
    assert project["title"] == "Imported chats"
    conversations = _by_kind(env.store, "CONVERSATION")
    assert sorted(c["title"] for c in conversations) == ["Launch plan", "Old chat"]
    from jarvis.memory.brain_map.engine import projects_of

    with env.store.read() as conn:
        for conversation in conversations:
            assert projects_of(conn, conversation["entity_id"]) == {project["entity_id"]}
