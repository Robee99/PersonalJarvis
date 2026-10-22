"""Read-only brain map MCP tools: the report format and the server registration."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.memory.brain_map import mcp_tools
from jarvis.memory.brain_map.service import BrainMapService
from jarvis.memory.brain_map.sources import AgentChatReader
from tests.fakes.fake_brain_map import ChatFixture, FakeClock

_SECTIONS = [
    "TITLE",
    "SUMMARY",
    "STATUS",
    "KEY DECISIONS",
    "OPEN TASKS",
    "BLOCKERS",
    "NEXT ACTIONS",
    "SOURCE REFERENCES",
]


@pytest.fixture
def service(tmp_path: Path):
    clock = FakeClock()
    data = tmp_path / "data"
    data.mkdir()
    chat = ChatFixture(data, clock)
    sid = chat.session(cwd=str(tmp_path / "falcon-app"))
    chat.say(sid, "We decided to use Qwen for the voice route.")
    chat.say(sid, "We're blocked on the API key.")
    chat.say(sid, "TODO: write the launch post.")
    svc = BrainMapService(
        data_dir=data, readers=[AgentChatReader(data / "agent_chat.db")], clock=clock
    )
    svc.reindex(trigger="test")
    yield svc
    svc.close()
    chat.close()


def test_make_report_has_the_sections_in_order(service: BrainMapService) -> None:
    text, is_error = mcp_tools.answer("make_report", {"project": "falcon-app"}, service)
    assert not is_error
    lines = text.splitlines()
    positions = [lines.index(section) for section in _SECTIONS]
    assert positions == sorted(positions)
    assert lines[1] == "falcon-app"
    assert lines[positions[2] + 1].startswith("RED")
    assert any("We decided to use Qwen for the voice route" in line for line in lines)
    refs = lines[positions[-1] + 1 :]
    assert any(
        "agent_chat" in r and "seq:2" in r and "“We're blocked on the API key.”" in r for r in refs
    )


def test_read_tools_answer(service: BrainMapService) -> None:
    summary, err = mcp_tools.answer("project_summary", {}, service)
    assert not err and "falcon-app" in summary and "[RED]" in summary
    tasks, err = mcp_tools.answer("open_tasks", {"project": "falcon-app"}, service)
    assert not err and "Write the launch post" in tasks
    blockers, err = mcp_tools.answer("blockers", {}, service)
    assert not err and "blocked on the API key" in blockers
    found, err = mcp_tools.answer("find_source", {"query": "Qwen"}, service)
    assert not err and "seq:1" in found
    action, err = mcp_tools.answer("next_action", {}, service)
    assert not err and action.startswith("Resolve blocker:")
    missing, err = mcp_tools.answer("make_report", {"project": "nope"}, service)
    assert err and "No project" in missing


def test_async_call_runs_off_the_loop(service: BrainMapService) -> None:
    text, is_error = asyncio.run(mcp_tools.call("blockers", {}, lambda: service))
    assert not is_error and "API key" in text


def test_server_offers_the_tools_to_a_chat_and_to_hermes_only() -> None:
    from jarvis.mcp import jarvis_tools_server as server

    def offered(session_id: str | None, taken: frozenset[str] = frozenset()) -> set[str]:
        token = server.CHAT_SESSION_REF.set(session_id)
        try:
            return {spec.name for spec in server._brain_map_offered(set(taken))}  # noqa: SLF001
        finally:
            server.CHAT_SESSION_REF.reset(token)

    assert offered("chat-1") == mcp_tools.TOOL_NAMES
    assert "blockers" not in offered("chat-1", frozenset({"blockers"}))
    # Hermes calls with no chat session: it is the brain that answers the person.
    assert offered(None) == mcp_tools.TOOL_NAMES
    # Society seats and task grants keep their own catalogs.
    assert offered("society:seat-1") == set()
    assert offered("task-123") == set()
