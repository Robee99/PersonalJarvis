"""ai_council hands a topic to the council extension in the owner's Chrome."""

from __future__ import annotations

import asyncio
import urllib.parse

import pytest

from jarvis.plugins.tool import ai_council
from jarvis.plugins.tool.ai_council import AiCouncilTool, council_url


def test_the_topic_and_judge_travel_in_the_chatgpt_address() -> None:
    url = council_url("Is tea better than coffee & why?", "claude")
    assert url.startswith("https://chatgpt.com/#")
    params = urllib.parse.parse_qs(url.split("#", 1)[1])
    assert params == {"jarvis-council": ["Is tea better than coffee & why?"], "judge": ["claude"]}


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(ai_council, "open_in_chrome", seen.append)
    return seen


def test_a_debate_opens_one_chrome_tab_with_claude_judging(opened: list[str]) -> None:
    result = asyncio.run(AiCouncilTool().execute({"topic": "Kolkata or Delhi for a week?"}, None))
    assert result.success and "Claude judges" in result.output
    assert len(opened) == 1 and "judge=claude" in opened[0]


def test_an_unknown_judge_falls_back_to_claude_and_no_topic_is_refused(
    opened: list[str],
) -> None:
    tool = AiCouncilTool()
    asyncio.run(tool.execute({"topic": "x", "judge": "someone"}, None))
    missing = asyncio.run(tool.execute({}, None))
    assert "judge=claude" in opened[0]
    assert not missing.success and len(opened) == 1


def test_sync_chats_asks_the_kept_extension_to_sync(opened: list[str]) -> None:
    result = asyncio.run(AiCouncilTool().execute({"action": "sync_chats"}, None))
    assert result.success and opened == ["http://127.0.0.1:18241/connect?sync=1&close=1"]
