"""/api/brain-map serves each project's health, the rule table, and the thread tree."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.memory.brain_map.health import HEALTH_RULE_IDS
from jarvis.memory.brain_map.service import BrainMapService
from jarvis.memory.brain_map.sources import AgentChatReader
from jarvis.ui.web.brain_map_routes import router
from tests.fakes.fake_brain_map import ChatFixture, FakeClock


@pytest.fixture
def client(tmp_path: Path):
    clock = FakeClock()
    data = tmp_path / "data"
    data.mkdir()
    chat = ChatFixture(data, clock)
    service = BrainMapService(
        data_dir=data, readers=[AgentChatReader(data / "agent_chat.db")], clock=clock
    )
    blocked = chat.session(cwd=str(tmp_path / "alpha"))
    chat.say(blocked, "We're blocked on the API key.")
    working = chat.session(cwd=str(tmp_path / "beta"))
    chat.say(working, "We decided to use Qwen. I need to write the benchmark notes.")
    app = FastAPI()
    app.include_router(router)
    app.state.brain_map = service
    with TestClient(app) as http:
        assert http.post("/api/brain-map/reindex").status_code == 200
        yield http
    chat.close()
    service.close()


def test_projects_carry_health_and_the_rule_table(client: TestClient) -> None:
    body = client.get("/api/brain-map/projects").json()
    assert [r["id"] for r in body["health_rules"]] == list(HEALTH_RULE_IDS)
    assert {r["colour"] for r in body["health_rules"]} == {"red", "yellow", "blue", "green"}
    by_title = {p["title"]: p for p in body["projects"]}
    alpha = by_title["alpha"]["health"]
    assert (alpha["colour"], alpha["rule"]) == ("red", "blocked")
    assert alpha["open_blockers"] == 1 and alpha["total"] == 1
    beta = by_title["beta"]["health"]
    assert beta["colour"] in ("yellow", "red")  # open work; red only once idle a week
    assert beta["open_tasks"] == 1 and beta["progress"] == 0.0


def test_project_detail_serves_the_thread_tree(client: TestClient) -> None:
    projects = client.get("/api/brain-map/projects").json()["projects"]
    beta_id = next(p["id"] for p in projects if p["title"] == "beta")
    detail = client.get(f"/api/brain-map/projects/{beta_id}").json()
    assert detail["health"]["rule"] in HEALTH_RULE_IDS
    (topic,) = detail["thread"]["branches"]
    assert topic["branch"] == "topic"
    assert sorted(c["branch"] for c in topic["children"]) == ["decision", "subtask"]
    assert detail["thread"]["counts"]["topic"] == 1
    assert detail["thread"]["hidden_branches"] == 0


def test_graph_projects_carry_health(client: TestClient) -> None:
    body = client.get("/api/brain-map/graph?limit=50").json()
    assert body["health_rules"][0]["id"] == "blocked"
    projects = [n for n in body["nodes"] if n["kind"] == "PROJECT"]
    assert projects and all(n["health"]["colour"] for n in projects)
