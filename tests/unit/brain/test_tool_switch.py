"""The tool switch: a few tools per turn, more switched on when the job needs them.

Live 2026-10-19 a 48-tool cap on the local brain hid every MCP tool, and
Roberto asked (2026-10-25) for a router that "only loads or fires em when
needed like a switch". Contract under test:

1. A turn opens with the core tools, the tools the request names, and a few
   siblings from the matched MCP server; everything else is hidden.
2. ``enable_tools`` switches more on, and the loop sends them the next round.
3. A hidden tool the model calls by its exact name is switched on and runs.
4. The manager puts local / small-window brains behind the switch and leaves
   a frontier cloud window alone, and ``off`` turns it off.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain.manager import BrainManager, _relevant_tool_names
from jarvis.brain.tool_switch import (
    ENABLE_TOOLS,
    EnableToolsTool,
    ToolSwitch,
    one_set_of_hands,
    wants_switch,
)
from jarvis.brain.tool_use_loop import ToolUseLoop
from jarvis.core.protocols import BrainDelta, BrainRequest, ToolResult


@dataclass
class _Tool:
    name: str
    description: str = "does a thing"
    schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    is_mcp_tool: bool = False
    risk_tier: str = "safe"
    calls: int = 0

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        del args, ctx
        self.calls += 1
        return ToolResult(success=True, output=f"{self.name} done")


class _RunsTools:
    """Executor fake: runs the tool itself, no gates."""

    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        return await tool.execute(args, None)


def _registry() -> dict[str, _Tool]:
    tools: dict[str, _Tool] = {
        "navigate": _Tool("navigate", "Open a section of the Jarvis app"),
        "search_web": _Tool("search_web", "Search the web for current facts"),
    }
    for i in range(40):
        tools[f"setting_{i}"] = _Tool(f"setting_{i}", f"Change setting number {i}")
    for verb in ("navigate", "snapshot", "click", "type", "tabs", "close", "pdf", "drag"):
        name = f"playwright/browser_{verb}"
        tools[name] = _Tool(name, f"Browser {verb} in the web browser page", is_mcp_tool=True)
    tools["spotify"] = _Tool("spotify", "Play music, songs and playlists on Spotify")
    return tools


def _switch(tools: dict[str, Any]) -> ToolSwitch:
    switch = ToolSwitch(tools, _relevant_tool_names)
    tools[ENABLE_TOOLS] = EnableToolsTool(switch)
    return switch


def test_a_turn_opens_with_core_request_tools_and_server_siblings() -> None:
    tools = _registry()
    switch = _switch(tools)

    switch.open_turn("open youtube in the browser", core=("navigate", "search_web"))

    on = set(switch.visible())
    assert {ENABLE_TOOLS, "navigate", "search_web"} <= on
    assert "playwright/browser_navigate" in on
    # The click and snapshot the next step needs come along with the server.
    assert {"playwright/browser_click", "playwright/browser_snapshot"} <= on
    assert "spotify" not in on
    assert not any(name.startswith("setting_") for name in on)
    assert len(on) < len(tools) // 3


def test_a_web_job_gets_the_browser_hands_even_when_every_browser_name_matches() -> None:
    # Live 2026-10-25: "use the browser" switched on close/emulate_media/
    # evaluate, never navigate or snapshot, and Gemma fell back to computer_use.
    tools = _registry()
    for verb in ("emulate_media", "evaluate", "drop", "find", "fill_form", "wait_for"):
        name = f"playwright/browser_{verb}"
        tools[name] = _Tool(name, "Browser tool", is_mcp_tool=True)
    switch = _switch(tools)

    switch.open_turn("use the browser", core=())

    hands = {"navigate", "snapshot", "click", "type", "fill_form", "wait_for"}
    assert {f"playwright/browser_{v}" for v in hands} <= set(switch.visible())


def test_a_shopping_request_opens_the_browser_without_naming_it() -> None:
    tools = _registry()
    switch = _switch(tools)

    switch.open_turn("cheapest 1kg chicken across grocery stores", core=())

    assert {"playwright/browser_navigate", "playwright/browser_snapshot"} <= set(switch.visible())


def test_a_shopping_request_gets_the_one_call_price_comparison_first() -> None:
    tools = _registry()
    tools["compare_prices"] = _Tool("compare_prices", "Compare grocery prices across stores")
    switch = _switch(tools)

    switch.open_turn("cheapest 1kg chicken across grocery stores", core=())

    assert switch.is_on("compare_prices")


def test_the_computer_volume_never_reaches_the_voice_volume_setting() -> None:
    # Live 2026-10-25 "set the system volume to 30 percent" turned the
    # assistant's own voice down instead.
    tools = _registry()
    tools["tts-volume-set"] = _Tool("tts-volume-set", "Set voice volume of speech")
    switch = _switch(tools)

    switch.open_turn("set the system volume to 30 percent", core=())
    assert not switch.is_on("tts-volume-set")

    switch.open_turn("make your voice volume quieter", core=())
    assert switch.is_on("tts-volume-set")


def test_a_web_job_leaves_screen_clicking_off() -> None:
    # Live 2026-10-25 Gemma had both and chose computer_use for a web page.
    tools = _registry()
    tools["computer_use"] = _Tool("computer_use", "Open the browser and click on screen")
    switch = _switch(tools)

    # The manager keeps computer_use on every turn it is registered for.
    switch.open_turn("open bigbasket.com in the browser", core=(), keep=("computer_use",))

    assert "playwright/browser_navigate" in switch.visible()
    assert not switch.is_on("computer_use")


def test_a_job_in_the_owners_open_chrome_keeps_screen_clicking() -> None:
    # Live 2026-10-26 "chrome is already opened click on roberto" meant his
    # Chrome profile picker, which the browser server's own window never has.
    tools = _registry()
    tools["computer_use"] = _Tool("computer_use", "Open the browser and click on screen")
    switch = _switch(tools)

    switch.open_turn(
        "chrome is already opened click on roberto profile then go to youtube",
        core=(),
        keep=("computer_use",),
    )

    assert switch.is_on("computer_use")
    assert "playwright/browser_navigate" in switch.visible()


def test_a_desktop_job_still_gets_screen_clicking() -> None:
    tools = _registry()
    tools["computer_use"] = _Tool("computer_use", "Click and type in a desktop app on screen")
    switch = _switch(tools)

    switch.open_turn("click save in the desktop app on screen", core=())

    assert switch.is_on("computer_use")


def test_a_request_with_no_web_words_gets_no_browser() -> None:
    tools = _registry()
    switch = _switch(tools)

    switch.open_turn("turn the volume down", core=())

    assert not any(name.startswith("playwright/") for name in switch.visible())


@pytest.mark.asyncio
async def test_enable_tools_switches_on_what_the_job_describes() -> None:
    tools = _registry()
    switch = _switch(tools)
    switch.open_turn("hello", core=("navigate",))
    assert not switch.is_on("spotify")

    result = await tools[ENABLE_TOOLS].execute({"need": "play a song on spotify"}, None)

    assert result.success
    assert "spotify" in result.output
    assert switch.is_on("spotify")


@pytest.mark.asyncio
async def test_enable_tools_says_so_when_nothing_matches() -> None:
    tools = _registry()
    switch = _switch(tools)
    switch.open_turn("", core=())

    result = await tools[ENABLE_TOOLS].execute({"need": "zzqx"}, None)

    assert result.success
    assert "No further tool" in result.output


class _EnableThenPlayBrain:
    """Round 1 asks for music tools, round 2 plays, round 3 answers."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            call = {"id": "c1", "name": ENABLE_TOOLS, "input": {"need": "play music on spotify"}}
        elif len(self.requests) == 2:
            call = {"id": "c2", "name": "spotify", "input": {}}
        else:
            yield BrainDelta(content="Playing.")
            yield BrainDelta(finish_reason="stop")
            return
        yield BrainDelta(tool_call=call)
        yield BrainDelta(finish_reason="tool_use")


@pytest.mark.asyncio
async def test_the_loop_sends_switched_on_tools_from_the_next_round() -> None:
    tools = _registry()
    switch = _switch(tools)
    switch.open_turn("put something on", core=("navigate",))
    brain = _EnableThenPlayBrain()
    loop = ToolUseLoop(brain, tools, _RunsTools(), tool_switch=switch)  # type: ignore[arg-type]

    await loop.run([], user_utterance="put something on")

    first = {t["name"] for t in brain.requests[0].tools}
    second = {t["name"] for t in brain.requests[1].tools}
    assert "spotify" not in first
    assert "spotify" in second
    assert len(first) < len(tools)
    assert tools["spotify"].calls == 1


class _CallsHiddenToolBrain:
    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(tool_call={"id": "c1", "name": "spotify", "input": {}})
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="Done.")
        yield BrainDelta(finish_reason="stop")


@pytest.mark.asyncio
async def test_a_hidden_tool_called_by_name_is_switched_on_and_runs() -> None:
    tools = _registry()
    switch = _switch(tools)
    switch.open_turn("hello", core=("navigate",))
    brain = _CallsHiddenToolBrain()
    loop = ToolUseLoop(brain, tools, _RunsTools(), tool_switch=switch)  # type: ignore[arg-type]

    await loop.run([], user_utterance="play spotify")

    assert tools["spotify"].calls == 1
    assert switch.is_on("spotify")


def test_switch_mode_truth_table() -> None:
    big = 200
    assert wants_switch("auto", tool_count=big, context_window=65_536, local=True)
    assert wants_switch("auto", tool_count=big, context_window=0, local=True)
    assert not wants_switch("auto", tool_count=big, context_window=1_000_000, local=False)
    assert not wants_switch("auto", tool_count=10, context_window=65_536, local=True)
    assert wants_switch("on", tool_count=10, context_window=1_000_000, local=False)
    assert not wants_switch("off", tool_count=big, context_window=65_536, local=True)


class _LocalBrain:
    name = "local-openai"
    context_window = 65_536
    max_tools = 48
    supports_vision = True


class _CloudBrain:
    name = "claude-api"
    context_window = 1_000_000
    max_tools = 0
    supports_vision = True


def _manager(mode: str = "auto") -> BrainManager:
    m = BrainManager.__new__(BrainManager)  # bypass heavy __init__
    m._tools = {}
    m._evidence_required_tool = None
    m._skill_turn_match = None
    m._config = SimpleNamespace(brain=SimpleNamespace(tool_switch=mode, tool_switch_core=None))
    return m


def test_manager_puts_a_local_brain_behind_the_switch() -> None:
    opened = _manager()._open_tool_switch(_registry(), _LocalBrain(), "open the browser")

    assert opened is not None
    surface, switch = opened
    assert ENABLE_TOOLS in surface
    assert switch.is_on("navigate")
    assert switch.is_on("playwright/browser_navigate")
    assert not switch.is_on("spotify")


def test_manager_leaves_a_frontier_window_alone_and_honours_off() -> None:
    assert _manager()._open_tool_switch(_registry(), _CloudBrain(), "open the browser") is None
    assert _manager("off")._open_tool_switch(_registry(), _LocalBrain(), "browser") is None


def _with_cua(tools: dict[str, Any]) -> dict[str, Any]:
    tools["computer_use"] = _Tool("computer_use", "Click and type in a desktop app on screen")
    tools["app_control"] = _Tool("app_control", "Press buttons and type in a desktop app")
    for verb in ("click", "type_text", "get_window_state", "launch_app", "browser_navigate"):
        name = f"cua-driver/{verb}"
        tools[name] = _Tool(name, f"{verb} in a desktop app window or browser", is_mcp_tool=True)
    return tools


def test_an_app_job_gets_app_control_instead_of_screen_clicking() -> None:
    # Live 2026-10-25 Gemma chose computer_use even when told to use CUA, and
    # copied example window ids out of the raw CUA tools when it had them.
    tools = _with_cua(_registry())
    switch = _switch(tools)

    switch.open_turn("open Calculator and press 7 plus 8", core=(), keep=("computer_use",))

    on = set(switch.visible())
    assert "app_control" in on
    assert "computer_use" not in on
    assert not any(name.startswith("cua-driver/") for name in on)


def test_a_web_job_never_gets_the_desktop_drivers_browser_tools() -> None:
    tools = _with_cua(_registry())
    switch = _switch(tools)

    switch.open_turn("open youtube.com in the browser", core=())

    assert switch.is_on("playwright/browser_navigate")
    assert not switch.is_on("cua-driver/browser_navigate")


@pytest.mark.asyncio
async def test_enable_tools_for_chrome_never_hands_over_the_desktop_drivers_browser_tools() -> None:
    # Live 2026-10-26 "enable_tools: control Chrome browser" switched on the
    # raw CUA browser tools; Gemma invented pid 12345 and target_1 and every
    # call failed. The browser server's tools, already on, are the answer.
    tools = _with_cua(_registry())
    for verb in ("get_browser_state", "browser_prepare", "browser_dialog"):
        name = f"cua-driver/{verb}"
        tools[name] = _Tool(name, f"Chrome browser {verb} with pid and window id", is_mcp_tool=True)
    switch = _switch(tools)
    switch.open_turn("go to chrome open youtube and play i mean it by g eazy", core=())
    assert switch.is_on("playwright/browser_navigate")

    result = await tools[ENABLE_TOOLS].execute({"need": "control Chrome browser"}, None)

    assert not any(n.startswith("cua-driver/") for n in switch.visible())
    assert "playwright/browser_navigate" in result.output
    assert "already on" in result.output


def test_chrome_tasks_open_only_the_owners_browser_tool() -> None:
    tools = _registry()
    tools["my_chrome"] = _Tool("my_chrome", "Drive the owner's own Chrome")
    tools["computer_use"] = _Tool("computer_use", "Click and type on screen")
    switch = _switch(tools)

    switch.open_turn(
        "go to chrome open youtube and play i mean it by g eazy", core=(), keep=("computer_use",)
    )

    assert set(switch.visible()) == {ENABLE_TOOLS, "my_chrome"}


@pytest.mark.parametrize("utterance", [
    "NOW IN THE SAME WINDOW CLICK ON NEW TAB AND GO TO LICIOUS",
    "In the same Chrome window open two tabs, Swiggy in one and BigBasket in the other",
])
def test_explicit_same_window_tabs_keep_the_desktop_operator(utterance):
    tools = _registry()
    tools["my_chrome"] = _Tool("my_chrome", "Drive the owner's own Chrome")
    tools["computer_use"] = _Tool("computer_use", "Click and type on screen")
    tools["app_control"] = _Tool("app_control", "Drive an app in the background")
    switch = _switch(tools)
    switch.open_turn(utterance, core=(), keep=("computer_use",))
    assert switch.is_on("computer_use")
    assert not switch.is_on("my_chrome")
    assert not switch.is_on("playwright/browser_navigate")


def _desktop_registry() -> dict[str, Any]:
    tools = _registry()
    tools["pc_pilot"] = _Tool("pc_pilot", "Drive any app or window on this PC: click, type, keys")
    tools["computer_use"] = _Tool("computer_use", "Control the screen with mouse and keyboard")
    tools["app_control"] = _Tool("app_control", "Press buttons in a desktop app")
    tools["my_chrome"] = _Tool("my_chrome", "Drive the owner's own Chrome browser")
    tools["society_browser"] = _Tool(
        "society_browser", "Use the society browser for web pages"
    )
    for verb in ("Snapshot", "Click", "Type", "Scroll", "App"):
        name = f"windows-desktop/{verb}"
        tools[name] = _Tool(name, f"{verb} a Windows desktop window app", is_mcp_tool=True)
    return tools


@pytest.mark.parametrize(
    "utterance",
    [
        "open windows settings and click on windows update",
        "open calculator and press 7 plus 8",
        "open a new tab in the same chrome window",
    ],
)
def test_the_pc_pilot_is_the_only_desktop_hands(utterance: str) -> None:
    # Native app and explicit same-window tasks stay on the desktop pilot.
    tools = _desktop_registry()
    switch = _switch(tools)

    switch.open_turn(utterance, core=("navigate", "pc_pilot"), keep=("computer_use",))

    on = set(switch.visible())
    assert "pc_pilot" in on
    assert not {"computer_use", "app_control", "my_chrome", "society_browser"} & on
    assert not any(n.startswith(("windows-desktop/", "playwright/")) for n in on)


@pytest.mark.parametrize("utterance", [
    "open chrome click on Roberto navigate to YouTube then play Praise the Lord",
    "open Windows PC settings and go to Apps Startup Apps",
])
def test_external_desktop_action_starts_with_only_the_pilot(utterance: str) -> None:
    tools = _desktop_registry()
    tools["gmail/search"] = _Tool("gmail/search", "Search Gmail", is_mcp_tool=True)
    tools["marketplace/browse"] = _Tool(
        "marketplace/browse", "Browse marketplace", is_mcp_tool=True
    )
    surface = one_set_of_hands(tools, utterance)
    switch = _switch(surface)

    switch.open_turn(utterance, keep=("computer_use",))

    assert set(switch.visible()) == {ENABLE_TOOLS, "pc_pilot"}


def test_explicit_computer_use_stays_the_only_desktop_driver() -> None:
    utterance = "use computer use to click on Roberto"
    surface = one_set_of_hands(_desktop_registry(), utterance)
    switch = _switch(surface)

    switch.open_turn(utterance, keep=("computer_use",))

    assert set(switch.visible()) == {ENABLE_TOOLS, "computer_use"}


@pytest.mark.parametrize(
    "utterance",
    [
        "open chrome go to Swiggy Instamart",
        "navigate browser tabs and open my logged-in Swiggy tab",
    ],
)
def test_owner_browser_tasks_use_browserskill_without_overlapping_hands(utterance: str) -> None:
    tools = _desktop_registry()
    tools["playwright/browser_navigate"] = _Tool(
        "playwright/browser_navigate", "Open a fresh browser", is_mcp_tool=True
    )
    # Production filters the full registry before constructing the switch.
    # Exercise that exact order: otherwise the pilot path hides my_chrome here.
    surface = one_set_of_hands(tools, utterance)
    switch = _switch(surface)

    switch.open_turn(utterance, core=("navigate", "pc_pilot"), keep=("computer_use",))

    on = set(switch.visible())
    assert on == {ENABLE_TOOLS, "my_chrome"}
    assert "playwright/browser_navigate" not in on
    assert not any(name.startswith("windows-desktop/") for name in on)


def test_a_request_naming_the_browser_server_keeps_it_beside_the_pilot() -> None:
    tools = _desktop_registry()
    switch = _switch(tools)

    switch.open_turn("use playwright to open example.com", core=("pc_pilot",))

    assert {"pc_pilot", "playwright/browser_navigate"} <= set(switch.visible())


@pytest.mark.parametrize(
    "utterance",
    [
        "use my_chrome to open YouTube",
        "use my Chrome to open YouTube",
        "use my browser to open YouTube",
        "use the browser skill to open YouTube",
    ],
)
def test_explicit_owner_chrome_survives_the_full_pilot_turn(utterance: str) -> None:
    tools = _desktop_registry()
    for name in (
        "playwright/browser_navigate",
        "playwright/browser_snapshot",
        "playwright/browser_click",
    ):
        tools[name] = _Tool(name, "Control a separate browser", is_mcp_tool=True)
    switch = _switch(tools)

    switch.open_turn(utterance, core=("navigate", "pc_pilot"))

    visible = set(switch.visible())
    assert {"my_chrome", "pc_pilot"} <= visible
    assert not any(name.startswith("playwright/") for name in visible)


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("use computer use to click the button", "computer_use"),
        ("use app_control to click the button", "app_control"),
        ("use Windows desktop MCP to click the button", "windows-desktop/Click"),
    ],
)
def test_explicit_desktop_backend_survives_the_full_pilot_turn(
    utterance: str, expected: str,
) -> None:
    tools = _desktop_registry()
    switch = _switch(tools)

    switch.open_turn(utterance, core=("pc_pilot",), keep=("computer_use",))

    visible = set(switch.visible())
    assert expected in visible
    if expected == "computer_use":
        assert "pc_pilot" not in visible


def test_explicit_app_control_for_chrome_survives_browser_selection() -> None:
    request = "use app_control to click the Chrome window"
    surface = one_set_of_hands(_desktop_registry(), request)
    switch = _switch(surface)
    switch.open_turn(request, core=("pc_pilot",))
    assert "app_control" in switch.visible()


def test_with_the_pilot_the_other_hands_leave_the_turn_for_good() -> None:
    """Live 2026-10-27: enable_tools brought back Playwright and my_chrome.

    Gemma then mixed refs between two browsers and typed into a terminal.
    """
    tools = _desktop_registry()
    tools["windows-desktop/PowerShell"] = _Tool(
        "windows-desktop/PowerShell", "Run PowerShell", is_mcp_tool=True
    )
    surface = one_set_of_hands(tools, "now go to youtube and search codeacus and play the video")
    switch = _switch(surface)
    switch.open_turn("try again", core=("pc_pilot",))

    added = switch.enable_for("navigate to YouTube and search", ["playwright/browser_type"])

    assert not {"computer_use", "app_control", "my_chrome"} & set(surface)
    assert not any(n.startswith("playwright/") for n in surface)
    assert not any(n.startswith("playwright/") or n == "my_chrome" for n in added)
    assert "windows-desktop/Click" not in surface
    assert "windows-desktop/PowerShell" in surface
    assert "pc_pilot" in surface


@pytest.mark.parametrize(
    ("utterance", "kept"),
    [
        ("use computer use to click on roberto", "computer_use"),
        ("use playwright to open example.com", "playwright/browser_navigate"),
        ("use my_chrome to open github", "my_chrome"),
        ("use windows desktop mcp to click start", "windows-desktop/Click"),
    ],
)
def test_hands_the_owner_names_stay(utterance: str, kept: str) -> None:
    assert kept in one_set_of_hands(_desktop_registry(), utterance)


def test_without_the_pilot_nothing_is_taken_away() -> None:
    tools = _desktop_registry()
    del tools["pc_pilot"]
    assert one_set_of_hands(tools, "open youtube") == tools


def test_youtube_website_request_does_not_offer_youtube_music_plugin():
    tools = _desktop_registry()
    tools["youtube_music"] = _Tool("youtube_music", "Play music")
    tools["spotify"] = _Tool("spotify", "Play music")
    utterance = "open chrome click on roberto navigate to youtube then play praise the lord"
    website_tools = one_set_of_hands(tools, utterance)
    assert "youtube_music" not in website_tools
    assert "spotify" not in website_tools
    music_tools = one_set_of_hands(tools, "play Praise the Lord in YouTube Music")
    assert "youtube_music" in music_tools


def test_chrome_profile_selection_before_navigation_uses_the_desktop_pilot():
    tools = _desktop_registry()
    tools["playwright/browser_navigate"] = _Tool(
        "playwright/browser_navigate", "Open a fresh browser", is_mcp_tool=True
    )
    tools["app_ui_click"] = _Tool("app_ui_click", "Click inside Jarvis")

    surface = one_set_of_hands(
        tools, "open chrome click on Roberto navigate to YouTube then play Praise the Lord"
    )

    assert "pc_pilot" in surface
    assert "my_chrome" not in surface
    assert "computer_use" not in surface
    assert "app_ui_click" not in surface
    assert "playwright/browser_navigate" not in surface


def test_scroll_followup_on_external_target_does_not_offer_jarvis_ui():
    tools = _desktop_registry()
    tools["app_ui_scroll"] = _Tool("app_ui_scroll", "Scroll Jarvis")
    tools["pc_pilot"].has_recent_target = True
    surface = one_set_of_hands(tools, "can u scroll down")
    assert "pc_pilot" in surface
    assert "app_ui_scroll" not in surface
    assert "app_ui_scroll" in one_set_of_hands(tools, "scroll down in Jarvis")
