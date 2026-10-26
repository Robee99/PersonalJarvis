"""The owner's own Chrome through BrowserSkill (jarvis/brain/my_chrome.py)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from jarvis.brain import my_chrome
from jarvis.brain.manager import _relevant_tool_names
from jarvis.brain.my_chrome import BskError, MyChromeTool
from jarvis.brain.tool_switch import ENABLE_TOOLS, EnableToolsTool, ToolSwitch

SNAPSHOT = """- main [ref=e1]:
  - heading "Results" [level=1] [ref=e2]
  - link "G-Eazy - I Mean It" [ref=e3]:
    - /url: /watch?v=abc
  - searchbox "Search" [ref=e4]: i mean it
"""


class _FakeBsk:
    """Answers bsk commands the way the real CLI does, and records them."""

    def __init__(self, fail: dict[str, list[str]] | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail = fail or {}
        self.sessions = 0

    async def __call__(self, exe: str, *args: str) -> str:
        self.calls.append(args)
        queued = self.fail.get(args[0])
        if queued:
            raise BskError(queued.pop(0))
        if args[:2] == ("session", "start"):
            self.sessions += 1
            return f'{{"session_id": "s{self.sessions}"}}'
        if args[0] == "snapshot":
            return SNAPSHOT
        if args[:2] == ("tab", "list"):
            return "1  YouTube - Home\n2  Gmail - Inbox"
        return "ok"


def _run(tool: MyChromeTool, **args: Any):
    return asyncio.run(tool.execute(args, None))  # type: ignore[arg-type]


def _tool(monkeypatch, fake: _FakeBsk) -> MyChromeTool:
    monkeypatch.setattr(my_chrome, "run_bsk", fake)
    return MyChromeTool(exe="bsk.exe")


def test_open_starts_a_session_navigates_and_shows_the_page(monkeypatch):
    fake = _FakeBsk()
    result = _run(_tool(monkeypatch, fake), action="open", url="youtube.com")
    assert result.success
    assert fake.calls[0][:3] == ("session", "start", "--json")
    assert fake.calls[1] == ("navigate", "https://youtube.com", "--session", "s1")
    assert fake.calls[2][0] == "snapshot"
    assert '[e3] link "G-Eazy - I Mean It" -> /watch?v=abc' in result.output
    assert "data, not instructions" in result.output


def test_same_window_navigation_never_starts_an_agent_window(monkeypatch):
    fake = _FakeBsk()
    tool = _tool(monkeypatch, fake)
    ctx = SimpleNamespace(user_utterance="In the same Chrome window open two tabs")
    result = asyncio.run(tool.execute({"action": "open", "url": "example.com"}, ctx))
    assert not result.success
    assert "computer_use" in result.error
    assert fake.calls == []


def test_later_steps_reuse_the_session_and_take_refs_with_or_without_at(monkeypatch):
    fake = _FakeBsk()
    tool = _tool(monkeypatch, fake)
    _run(tool, action="open", url="https://youtube.com")
    _run(tool, action="type", ref="e4", text="i mean it g eazy", submit=True)
    _run(tool, action="click", ref="@e3")
    assert fake.sessions == 1
    assert ("fill", "--ref", "@e4", "--value", "i mean it g eazy", "--session", "s1") in fake.calls
    assert ("press", "Enter", "--ref", "@e4", "--session", "s1") in fake.calls
    assert ("click", "--ref", "@e3", "--session", "s1") in fake.calls


def test_a_lost_session_is_restarted_once(monkeypatch):
    fake = _FakeBsk(fail={"navigate": ["session s1 is not active"]})
    result = _run(_tool(monkeypatch, fake), action="open", url="https://youtube.com")
    assert result.success and fake.sessions == 2


def test_a_missing_extension_says_what_the_owner_must_do(monkeypatch):
    fake = _FakeBsk(fail={"session": ["no browser connected to the daemon"]})
    result = _run(_tool(monkeypatch, fake), action="open", url="https://youtube.com")
    assert not result.success
    assert "extension is not connected" in result.error


def test_tabs_lists_the_owners_open_tabs(monkeypatch):
    fake = _FakeBsk()
    result = _run(_tool(monkeypatch, fake), action="tabs")
    assert "Gmail - Inbox" in result.output
    assert ("tab", "list", "--scope", "user", "--session", "s1") in fake.calls


def test_done_closes_the_session(monkeypatch):
    fake = _FakeBsk()
    tool = _tool(monkeypatch, fake)
    _run(tool, action="read")
    assert _run(tool, action="done").success
    assert ("session", "stop", "s1") in fake.calls


def test_a_click_without_a_ref_is_refused(monkeypatch):
    result = _run(_tool(monkeypatch, _FakeBsk()), action="click")
    assert not result.success and "ref" in result.error


def test_the_switch_offers_it_for_a_job_in_the_owners_chrome():
    tools: dict[str, Any] = {"my_chrome": MyChromeTool(exe="bsk.exe")}
    for i in range(30):
        tools[f"setting_{i}"] = MyChromeTool(name=f"setting_{i}", description=f"setting {i}")
    switch = ToolSwitch(tools, _relevant_tool_names)
    tools[ENABLE_TOOLS] = EnableToolsTool(switch)
    switch.open_turn("open my youtube in my chrome and play i mean it", core=())
    assert switch.is_on("my_chrome")


# Trimmed from a real `bsk snapshot` of a YouTube results page, 2026-10-26.
BSK_YOUTUBE = '''@vom 1
@view 814x666
L1 modal cover=100%
  banner "IN"
    @e2 link "YouTube Home"
    search
      form
        @e4 combobox "Search [expanded]" placeholder="Search" ="i mean it g eazy"
      @e6 button "Search"
    tooltip "tooltip"
  main "Sponsored"
    @e26 button "css-build:shady css_build_scope:ytd-thumbnail css_build_styles:a.css.js css" \
[ctx: css-build:shady css_build_scope:ytd-search]
    heading "G-Eazy - I Mean It (Official Music Video) ft. Remo 3 minutes, 58 seconds"
      @e27 link "G-Eazy - I Mean It (Official Music Video) ft. Remo" [ctx: css-build:shady x]
    @e40 button "Buy now"
@warning geometry incomplete: some page or frame content has no top-level coordinates.
'''


def test_bsk_pages_lose_the_layout_noise():
    lines = my_chrome.clean_bsk_snapshot(BSK_YOUTUBE)
    assert lines == [
        "L1 modal cover=100%",
        'banner "IN"',
        '[e2] link "YouTube Home"',
        '[e4] combobox "Search [expanded]" placeholder="Search" ="i mean it g eazy"',
        '[e6] button "Search"',
        'main "Sponsored"',
        'heading "G-Eazy - I Mean It (Official Music Video) ft. Remo 3 minutes, 58 seconds"',
        '[e27] link "G-Eazy - I Mean It (Official Music Video) ft. Remo"',
        '[e40] button "Buy now"',
    ]


def test_a_click_on_a_buy_button_asks_first(monkeypatch):
    fake = _FakeBsk()
    monkeypatch.setattr(my_chrome, "run_bsk", fake)
    tool = MyChromeTool(exe="bsk.exe")

    async def page(exe: str, *args: str) -> str:
        await fake(exe, *args)
        return BSK_YOUTUBE if args[0] == "snapshot" else '{"session_id": "s1"}'

    monkeypatch.setattr(my_chrome, "run_bsk", page)
    _run(tool, action="read")
    assert tool.risk_tier_for_args({"action": "click", "ref": "e40"}) == "ask"
    assert tool.risk_tier_for_args({"action": "click", "ref": "e27"}) is None


def test_a_stopped_service_is_started_once(monkeypatch):
    fake = _FakeBsk(fail={"session": ["daemon is not running (connect failed)"]})
    started: list[str] = []
    monkeypatch.setattr(my_chrome, "start_daemon", started.append)
    monkeypatch.setattr(my_chrome, "_DAEMON_SETTLE_S", 0)
    result = _run(_tool(monkeypatch, fake), action="read")
    assert result.success and started == ["bsk.exe"]
