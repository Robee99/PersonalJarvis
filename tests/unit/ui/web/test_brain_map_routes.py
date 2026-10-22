"""/api/brain-map/* return the documented shapes (no network, tmp stores)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.memory.brain_map.service import BrainMapService
from jarvis.memory.brain_map.sources import AgentChatReader, MissionReader
from jarvis.ui.web.brain_map_routes import router
from tests.fakes.fake_brain_map import ChatFixture, FakeClock, missions_db, put_mission


@pytest.fixture
def client(tmp_path: Path):
    clock = FakeClock()
    data = tmp_path / "data"
    data.mkdir()
    chat = ChatFixture(data, clock)
    missions = missions_db(data / "missions.db")
    sid = chat.session(cwd=str(tmp_path / "orb-project"))
    chat.say(sid, "We decided to use Qwen. We're blocked on the API key.")
    put_mission(missions, "m-9", "Benchmark orb-project models", "FAILED", clock.ms)
    service = BrainMapService(
        data_dir=data,
        readers=[AgentChatReader(data / "agent_chat.db"), MissionReader(data / "missions.db")],
        clock=clock,
    )
    app = FastAPI()
    app.include_router(router)
    app.state.brain_map = service
    with TestClient(app) as test_client:
        test_client.sid = sid  # type: ignore[attr-defined]
        yield test_client
    service.close()
    chat.close()
    missions.close()


def test_reindex_then_projects_list(client: TestClient) -> None:
    run = client.post("/api/brain-map/reindex")
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["trigger"] == "api" and body["segments_processed"] == 1

    listed = client.get("/api/brain-map/projects").json()
    assert listed["last_run"]["segments_processed"] == 1
    (project,) = listed["projects"]
    assert project["kind"] == "PROJECT" and project["title"] == "orb-project"
    assert project["status"] == "red"
    assert set(project["counts"]) == {
        "conversations",
        "open_tasks",
        "open_blockers",
        "decisions",
        "suggested",
    }
    assert project["counts"]["open_blockers"] == 1
    assert project["priority_reason"]


def test_project_detail_shape(client: TestClient) -> None:
    client.post("/api/brain-map/reindex")
    project_id = client.get("/api/brain-map/projects").json()["projects"][0]["id"]
    detail = client.get(f"/api/brain-map/projects/{project_id}").json()
    for key in (
        "summary",
        "state",
        "timeline",
        "decisions",
        "tasks",
        "blockers",
        "files",
        "branches",
        "agents",
        "reports",
        "next_actions",
        "priority",
        "conversations",
    ):
        assert key in detail, key
    assert detail["decisions"][0]["title"] == "We decided to use Qwen"
    assert detail["blockers"][0]["status"] == "red"
    # The failed mission names the project once: kept, but only as a suggestion.
    assert detail["tasks"][0]["assignment"] == "suggested"
    assert detail["next_actions"][0]["action"].startswith("Resolve blocker:")
    assert detail["state"]["status"] == "red"
    assert client.get("/api/brain-map/projects/project:nope").status_code == 404


def test_graph_and_entity_detail(client: TestClient) -> None:
    client.post("/api/brain-map/reindex")
    graph = client.get("/api/brain-map/graph").json()
    kinds = {n["kind"] for n in graph["nodes"]}
    assert {"PROJECT", "CONVERSATION", "DECISION", "BLOCKER", "TASK"} <= kinds
    assert all(n["color"].startswith("#") for n in graph["nodes"])
    assert {e["kind"] for e in graph["edges"]} >= {"part_of", "decided_in", "blocks"}
    blocker = next(n for n in graph["nodes"] if n["kind"] == "BLOCKER")
    detail = client.get(f"/api/brain-map/entities/{blocker['id']}").json()
    (evidence,) = detail["evidence"]
    assert evidence["quote"] == "We're blocked on the API key."
    assert evidence["source"]["conversation_id"] == client.sid  # type: ignore[attr-defined]
    assert evidence["source"]["message_ref"] == "seq:1"
    assert evidence["source"]["ts_ms"] > 0
    assert detail["status_history"][0]["status"] == "red"
    assert client.get("/api/brain-map/entities/none").status_code == 404
