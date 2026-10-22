"""Project health: every rule of the table, its order, and its parity with the API and the Orb."""

from __future__ import annotations

import re
import typing
from pathlib import Path

import pytest

from jarvis.memory.brain_map import query
from jarvis.memory.brain_map.constants import DAY_MS
from jarvis.memory.brain_map.health import (
    HEALTH_RULE_IDS,
    HEALTH_RULES,
    WorkItem,
    project_health,
)
from jarvis.memory.brain_map.indexer import (
    BrainMapIndexer,
    project_id_for_key,
    project_key_for_path,
)
from jarvis.memory.brain_map.sources import AgentChatReader, MissionReader
from jarvis.memory.brain_map.store import BrainMapStore
from jarvis.memory.brain_map.thread import BRANCH_KINDS
from jarvis.ui.web import brain_map_routes
from tests.fakes.fake_brain_map import ChatFixture, FakeClock, missions_db

NOW = 1_790_856_000_000
REPO = Path(__file__).resolve().parents[4]
ORB_TYPES = REPO / "jarvis/ui/web/frontend/src/components/orb/brainMap.ts"


def _health(status="blue", reason="", last=NOW, work=(), now=NOW):
    return project_health(
        status=status,
        status_reason=reason,
        last_activity_ms=last,
        work=[WorkItem(k, s) for k, s in work],
        now_ms=now,
    )


# ---- the rule table, one row at a time --------------------------------------


def test_an_open_red_blocker_is_red_blocked_even_when_everything_else_is_done() -> None:
    h = _health(status="red", work=[("BLOCKER", "red"), ("TASK", "green"), ("TASK", "green")])
    assert (h.colour, h.rule) == ("red", "blocked")
    assert h.reason == "blocked: 1 open blocker; 2 of 3 items done"
    assert (h.done, h.total, h.open_blockers, h.open_tasks) == (2, 3, 1, 0)


def test_a_failed_item_without_a_blocker_is_red_failed_with_the_status_reason() -> None:
    h = _health(status="red", reason="task “Deploy”: mission failed", work=[("TASK", "red")])
    assert (h.colour, h.rule, h.reason) == ("red", "failed", "task “Deploy”: mission failed")


def test_green_status_with_nothing_open_is_verified() -> None:
    h = _health(status="green", reason="all 2 items done or closed", work=[("TASK", "green")] * 2)
    assert (h.colour, h.rule) == ("green", "verified")
    assert h.progress == 1.0
    assert h.reason == "verified: all 2 items done or closed"


def test_green_status_is_never_trusted_while_work_is_open() -> None:
    # A wiki page saying "status: done" over a still-open task: not completed.
    h = _health(
        status="green", reason="wiki project done", work=[("TASK", "yellow"), ("TASK", "green")]
    )
    assert h.colour != "green"
    assert (h.colour, h.rule) == ("yellow", "partial")


def test_incomplete_with_little_progress_and_idle_is_red_stalled() -> None:
    h = _health(
        status="yellow",
        last=NOW - 10 * DAY_MS,
        work=[("TASK", "yellow"), ("TASK", "yellow"), ("TASK", "yellow"), ("TASK", "yellow")],
    )
    assert (h.colour, h.rule) == ("red", "stalled")
    assert h.reason == "incomplete: 0 of 4 items done; no activity since 2026-09-21"
    assert h.progress == 0.0


def test_little_progress_but_touched_this_week_is_partial_not_stalled() -> None:
    h = _health(status="blue", last=NOW - DAY_MS, work=[("TASK", "blue")] * 4)
    assert (h.colour, h.rule) == ("yellow", "partial")


def test_a_quarter_done_and_idle_is_partial_not_stalled() -> None:
    work = [("TASK", "green"), ("TASK", "yellow"), ("TASK", "yellow"), ("TASK", "yellow")]
    h = _health(status="yellow", last=NOW - 30 * DAY_MS, work=work)
    assert (h.colour, h.rule, h.progress) == ("yellow", "partial", 0.25)
    assert h.reason == "in progress: 1 of 4 items done"


def test_a_waiting_blocker_is_open_work_not_a_block() -> None:
    h = _health(status="yellow", work=[("BLOCKER", "yellow"), ("TASK", "green")])
    assert (h.colour, h.rule, h.open_blockers) == ("yellow", "partial", 1)


def test_all_tracked_work_done_but_not_verified_is_blue_review() -> None:
    h = _health(status="blue", work=[("TASK", "green"), ("BLOCKER", "green")])
    assert (h.colour, h.rule) == ("blue", "review")
    assert h.reason == "under review: all 2 items done, not verified yet"


def test_recent_activity_with_no_tracked_work_is_blue_active() -> None:
    h = _health(status="blue", last=NOW - DAY_MS)
    assert (h.colour, h.rule, h.progress) == ("blue", "active", None)
    assert h.reason == "active: last activity 2026-09-30"


def test_nothing_open_nothing_verified_and_quiet_is_yellow_idle() -> None:
    h = _health(status="yellow", reason="no activity recorded yet", last=0)
    assert (h.colour, h.rule) == ("yellow", "idle")
    assert h.reason == "idle: nothing open or verified; last activity never"


def test_context_kinds_never_move_progress() -> None:
    h = _health(
        status="blue", work=[("DECISION", "green"), ("MILESTONE", "green"), ("TASK", "blue")]
    )
    assert (h.done, h.total, h.progress) == (0, 1, 0.0)


@pytest.mark.parametrize("colour", ["red", "yellow", "blue", "green"])
def test_every_colour_has_at_least_one_rule(colour: str) -> None:
    assert any(r.colour == colour for r in HEALTH_RULES)


def test_rule_order_is_fixed() -> None:
    assert HEALTH_RULE_IDS == (
        "blocked",
        "failed",
        "verified",
        "stalled",
        "partial",
        "review",
        "active",
        "idle",
    )


# ---- parity: Python ↔ routes ↔ the Orb ---------------------------------------


def test_route_literals_match_the_python_vocabulary() -> None:
    assert typing.get_args(brain_map_routes.HealthRuleLiteral) == HEALTH_RULE_IDS
    assert typing.get_args(brain_map_routes.ThreadBranchLiteral) == BRANCH_KINDS


def _ts_tuple(name: str) -> tuple[str, ...]:
    text = ORB_TYPES.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \[(.*?)\] as const", text, re.DOTALL)
    assert match, f"{name} not found in {ORB_TYPES}"
    return tuple(re.findall(r'"([a-z]+)"', match.group(1)))


def test_orb_typescript_mirrors_the_rule_ids_and_branch_kinds() -> None:
    assert _ts_tuple("HEALTH_RULE_IDS") == HEALTH_RULE_IDS
    assert _ts_tuple("BRANCH_KINDS") == BRANCH_KINDS


# ---- end to end over the real indexer -----------------------------------------


@pytest.fixture
def env(tmp_path: Path):
    clock = FakeClock()
    data = tmp_path / "data"
    data.mkdir()
    chat = ChatFixture(data, clock)
    missions = missions_db(data / "missions.db")
    store = BrainMapStore(data / "jarvis.db")
    indexer = BrainMapIndexer(
        store,
        [AgentChatReader(data / "agent_chat.db"), MissionReader(data / "missions.db")],
        clock=clock,
    )
    yield clock, chat, missions, store, indexer, tmp_path
    chat.close()
    missions.close()
    store.close()


def _pid(path: Path) -> str:
    return project_id_for_key(project_key_for_path(str(path)))


def test_indexed_projects_get_health_from_their_real_state(env) -> None:
    clock, chat, _missions, store, indexer, tmp = env
    blocked = chat.session(cwd=str(tmp / "alpha"))
    chat.say(blocked, "We're blocked on the API key.")
    working = chat.session(cwd=str(tmp / "beta"))
    chat.say(working, "I need to write the landing page copy.")
    talking = chat.session(cwd=str(tmp / "gamma"))
    chat.say(talking, "We decided to use Qwen for the local route.")
    indexer.run(trigger="test")

    projects = {p["title"]: p for p in query.list_projects(store, now_ms=clock.ms)}
    assert projects["alpha"]["health"]["colour"] == "red"
    assert projects["alpha"]["health"]["rule"] == "blocked"
    assert projects["beta"]["health"]["rule"] == "partial"
    assert projects["beta"]["health"]["colour"] == "yellow"
    assert projects["gamma"]["health"]["rule"] == "active"
    assert projects["gamma"]["health"]["colour"] == "blue"

    # Said "done": the task closes with evidence, and the project is reviewable,
    # not verified, while the conversation itself is still active.
    clock.advance(seconds=60)
    chat.say(working, "Done with the landing page copy.")
    indexer.run(trigger="test")
    beta = query.project_detail(store, _pid(tmp / "beta"), now_ms=clock.ms)
    assert beta["health"]["progress"] == 1.0
    assert beta["health"]["rule"] == "review"


def test_closed_work_in_a_quiet_project_is_verified_green_and_on_the_graph(env) -> None:
    clock, chat, _missions, store, indexer, tmp = env
    sid = chat.session(cwd=str(tmp / "delta"))
    chat.say(sid, "I need to update the release notes.")
    clock.advance(seconds=60)
    chat.say(sid, "Done with the release notes.")
    clock.advance(days=8)  # the chat goes quiet: nothing is left open
    indexer.run(trigger="test")

    detail = query.project_detail(store, _pid(tmp / "delta"), now_ms=clock.ms)
    assert detail["status"] == "green"
    assert (detail["health"]["colour"], detail["health"]["rule"]) == ("green", "verified")
    assert detail["health"]["progress"] == 1.0

    graph = query.graph(store, now_ms=clock.ms)
    assert graph["health_rules"][0]["id"] == "blocked"
    (node,) = [n for n in graph["nodes"] if n["kind"] == "PROJECT"]
    assert node["health"] == detail["health"]
    assert all(n["health"] is None for n in graph["nodes"] if n["kind"] != "PROJECT")
