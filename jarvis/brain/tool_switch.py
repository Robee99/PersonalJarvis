"""The tool switch: a turn sees a few tools, and switches on more when it needs them.

A small local model (Gemma 4 E4B, Nemotron Bolt E4B) chooses badly from a
long tool list and pays for every schema in its context window: live
2026-10-19 the registry held 228 tools and a 48-tool cap hid every MCP tool.
Trimming after the fact decides what the model loses; this decides what it
gets. Each turn opens with

* the **core** hands every turn may need (navigate the app, read the screen,
  open an app, search, remember),
* the tools the request itself names or describes (``rank``, the manager's
  word match), with a few siblings from the same MCP server so "open YouTube
  in the browser" also gets the snapshot and click it will need next,
* whatever a gate mandated this turn (``keep``),

plus one meta-tool, :data:`ENABLE_TOOLS`, that switches more on mid-turn by
describing the job. A hidden tool the model calls by its exact name is
switched on and runs, so a model that already knows the name never pays a
round trip. Everything stays registered and gated by the executor exactly as
before; the switch changes only which schemas go on the wire.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from jarvis.core.protocols import ExecutionContext, RiskTier, Tool, ToolResult

log = logging.getLogger(__name__)

#: The meta-tool's registered name.
ENABLE_TOOLS = "enable_tools"

#: Built-in tools every turn starts with when they are registered. The app's
#: own sections, the screen, apps and windows, the web, and memory: the
#: requests a voice assistant gets most, each answerable without a lookup.
DEFAULT_CORE: tuple[str, ...] = (
    "navigate",
    "app_ui_snapshot",
    "app_ui_click",
    "app_ui_type",
    "app_ui_scroll",
    "pc_pilot",
    "open_app",
    "window_control",
    "system_volume",
    "screen_snapshot",
    "find_files",
    "search_web",
    "wiki-recall",
    "remember",
    "memory_chats",
    "run-skill",
)

#: The browser tools a web job uses, in the order it uses them. Live
#: 2026-10-25 "use the browser" matched every ``browser_*`` name equally and
#: switched on close/emulate_media/evaluate, so Gemma had no way to open a page
#: and fell back to ``computer_use``, which clicked the wrong app.
BROWSER_HANDS: tuple[str, ...] = (
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_fill_form",
    "browser_wait_for",
)
#: Tools a request only gets when it says one of these words. Live 2026-10-25
#: "set the system volume to 30 percent" ran tts-volume-set and turned the
#: assistant's own voice down while the computer stayed as loud.
_ONLY_WHEN_SAID: dict[str, re.Pattern[str]] = {
    "tts-volume-set": re.compile(r"\b(voice|speak\w*|talk\w*|your)\b", re.IGNORECASE),
}
#: The webcam tool's name, and what asks for it. Live 2026-10-26 "what am I
#: holding in my hand" had no camera on and Gemma said it could not see.
CAMERA = "camera"
_CAMERA_WORDS = re.compile(
    r"\b(?:webcam|camera|holding|hold(?:ing)?\s+up|in\s+my\s+hands?|look\s+at\s+me|see\s+me|"
    r"how\s+do\s+i\s+look|what\s+am\s+i\s+wearing|my\s+(?:face|shirt|outfit|posture)|"
    r"what(?:'s|\s+is)\s+this\s+(?:i'm|i\s+am)\s+(?:holding|showing))\b",
    re.IGNORECASE,
)


def wants_camera(text: str) -> bool:
    """Whether a request asks Jarvis to look through the webcam."""
    return bool(_CAMERA_WORDS.search(text or ""))
#: Pixel-level desktop control, left off for a web job the browser can do.
SCREEN_HANDS = "computer_use"
#: Words that mean the job happens on a web page.
_WEB_WORDS = re.compile(
    r"\b(browser|website|web\s*site|web\s*page|webpage|online|shop\w*|store|stores|"
    r"price\w*|cheap\w*|afford\w*|grocer\w*|order|checkout|form|forms|url|"
    r"chrome|edge|firefox)\b|https?://|\.(com|in|org|net|io)\b",
    re.IGNORECASE,
)
#: Words that point at the person's own, already open browser: its profiles,
#: sign-ins and tabs live only there, never in the browser server's own
#: window. "chrome is already opened click on roberto" means his Chrome
#: profile picker (live 2026-10-26), which only screen control can reach.
_OWN_BROWSER = re.compile(
    r"\b(already\s+open(ed)?|my\s+(own\s+)?(chrome|browser|edge|firefox|tabs?)|"
    r"profiles?|signed\s+in|logged\s+in|this\s+(tab|page|window)|current\s+(tab|page)|"
    r"open\s+tabs?|(?:chrome|browser|edge|firefox)\s+tabs?)\b",
    re.IGNORECASE,
)


#: The owner's real Chrome through BrowserSkill (my_chrome.py).
OWN_BROWSER_HANDS = "my_chrome"
#: "go to chrome and open youtube" also means his Chrome, though it is no
#: reason to keep screen clicking on (live 2026-10-26).
_SAYS_CHROME = re.compile(
    r"\b(go\s+to|in|on|into|use|using|with|open|through|via)\s+(google\s+)?chrome\b",
    re.IGNORECASE,
)
# The Chrome profile chooser is browser chrome, outside the page DOM. A task
# that clicks there before navigating needs the desktop pilot for that first
# selection; BrowserSkill can take over for ordinary Chrome/site requests.
_CHROME_LAUNCH_CLICK_BEFORE_NAVIGATION = re.compile(
    r"\b(?:open|launch|start)\s+(?:google\s+)?chrome\b"
    r".{0,100}?\b(?:click|select|choose|pick)\s+(?:on\s+)?(?:the\s+)?[\w'.-]+"
    r".{0,80}?\b(?:navigate|go\s+to|open|search)\b",
    re.IGNORECASE,
)


def wants_own_browser(text: str) -> bool:
    """Whether a request is about the person's own open browser, not a fresh one."""
    return bool(text and _OWN_BROWSER.search(text))


def wants_browser_window_control(text: str) -> bool:
    """An existing-window constraint cannot be met by a separate agent browser."""
    return bool(
        re.search(
            r"\b(?:same|this|current|existing)\s+"
            r"(?:(?:google\s+)?chrome\s+|browser\s+)?window\b", text, re.IGNORECASE,
        )
        and re.search(r"\b(?:open|add|create|click|press|switch|close)\b", text, re.IGNORECASE)
        and re.search(
            r"\b(?:browser|chrome|firefox|edge)\b|\bnew\s+tabs?\b", text, re.IGNORECASE,
        )
    )


#: A request about another program's window. Live 2026-10-26 "open Windows PC
#: settings, click on Windows Update": Settings was launched, then the clicks
#: went to app_ui_click, which only sees the Jarvis window, and one landed on a
#: row of Jarvis's own chat.
_OTHER_PROGRAM = re.compile(
    r"\b(?:windows|pc|system|computer|laptop)\s+settings?\b"
    r"|\bsettings\s+(?:app|on\s+(?:my|the)\s+(?:pc|computer|laptop))\b"
    r"|\bwindows\s+update\b|\bcontrol\s+panel\b|\bdevice\s+manager\b"
    r"|\btask\s+manager\b|\bfile\s+explorer\b"
    r"|\b(?:chrome|edge|firefox|brave|opera|notepad|calculator|excel|"
    r"powerpoint|outlook|spotify|whatsapp|telegram|discord|vs\s*code|explorer)\b",
    re.IGNORECASE,
)
_JARVIS_NAMED = re.compile(
    r"\b(?:jarvis|this\s+app|your\s+(?:app|screen|window|settings))\b", re.IGNORECASE,
)
#: Pointer words: the request acts on controls inside a window.
_POINTER_WORDS = re.compile(
    r"\b(?:click\w*|tap|press|select|choose|type|typing|scroll\w*|toggle|check\s+for)\b",
    re.IGNORECASE,
)
_EXTERNAL_ACTION = re.compile(
    r"\b(?:open|launch|start|navigate|go\s+to|switch\s+to|close|move|resize|"
    r"maximize|minimize)\b",
    re.IGNORECASE,
)
#: The desktop server tools a pointer job in another program needs, in order.
_DESKTOP_POINTER = ("Snapshot", "Click", "Type", "Scroll")


def names_other_program(text: str) -> bool:
    """Whether ``text`` is about another program's window, not the Jarvis app."""
    return bool(text and _OTHER_PROGRAM.search(text) and not _JARVIS_NAMED.search(text))


#: The one desktop driver (pc_pilot.py): any app, window, Settings page or
#: site, clicked and typed by name. When it is registered, the other hands
#: for another program's window stay off the opening surface: live
#: 2026-10-26 Gemma failed differently with each of computer_use, app_control,
#: the raw Windows-MCP and CUA servers and app_ui_click.
PILOT = "pc_pilot"
#: Words that keep a web job on the browser servers instead of the pilot.
_SAYS_BROWSER_SERVER = re.compile(
    r"\b(playwright|headless|agent\s+browser|browser\s+server|"
    r"society[\s_-]*browser|compare\s+prices?)\b",
    re.I,
)

#: Desktop-server tools that point, click, type or launch: the pilot's job.
#: Their servers' other tools (PowerShell, files, clipboard) stay.
_POINTER_TAILS = frozenset({
    "App", "Click", "Type", "Scroll", "Move", "Shortcut", "Snapshot", "MultiSelect",
    "MultiEdit", "Wait",
})
#: The hands that stay out when the pilot is here, unless the owner names them.
_SAYS_SCREEN_HANDS = re.compile(r"\bcomputer[\s_-]*use\b|\bscreen\s+control\b", re.I)
_SAYS_APP_HANDS = re.compile(r"\bapp[\s_-]*control\b|\bcua[\s_-]*driver\b", re.I)
_SAYS_MY_CHROME = re.compile(
    r"\bmy[_\s]+chrome\b|\bmy\s+(?:own\s+)?(?:google\s+)?chrome\b|"
    r"\bmy\s+browser\b|\bbrowser\s*skill\b",
    re.I,
)


def wants_browser_skill(text: str) -> bool:
    """Whether a request names Chrome or asks for the owner's browser or tabs."""
    text = text or ""
    return bool(
        _SAYS_MY_CHROME.search(text)
        or _SAYS_CHROME.search(text)
        or wants_own_browser(text)
    )


#: Live 2026-10-27 "open youtube.com then play i mean it by g eazy" opened
#: YouTube and then searched a Spotify that was never connected.
_SAYS_YOUTUBE = re.compile(r"\byou\s*tu+b+e?\b", re.I)
_SAYS_SPOTIFY = re.compile(r"\bspotify\b", re.I)
#: GEV's tool that moves the globe. Live 2026-10-27 "fly me to Mumbai in God's
#: Eye View" got eight of GEV's data tools ranked above it (every one of them
#: shares "gods eye view"), so Gemma answered with rocket launches.
GEV_VIEW_TAIL = "show_in_gods_eye_view"
_SAYS_GEV = re.compile(
    r"\bgods?['\u2019]?\s*s?\s*eye\b|\bgev\b|\bglobe\b|\bworld\s+(?:screen|map|view)\b"
    r"|\bfly\s+(?:me\s+)?(?:to|over)\b",
    re.I,
)


def pinned_for_request(tools: Iterable[str], query: str) -> list[str]:
    """Tools a request needs whatever the word ranking says, best first."""
    if not _SAYS_GEV.search(query or ""):
        return []
    return [n for n in tools if n.rsplit("/", 1)[-1] == GEV_VIEW_TAIL]


def one_set_of_hands(surface: dict[str, Tool], query: str) -> dict[str, Tool]:
    """With the PC pilot registered, give each task one matching set of hands.

    Live 2026-10-27 "go to youtube, search X and play the video" went through
    computer_use, my_chrome, the Playwright browser, Windows-MCP and the pilot
    in one turn, each failing its own way (a ref from another browser's page,
    an about:blank tab, a click in a Hermes terminal). The owner asked for
    the extra layers to go. The pilot opens apps, Settings pages and sites
    in the owner's own browser and works them by name, so the others leave
    the turn completely: ``enable_tools`` cannot bring them back either. A
    request for Chrome or the owner's open browser tabs uses BrowserSkill as
    its only browser hand. Explicit computer-use, named browser-server and
    same-window requests keep their named surface, including explicitly named
    app control and Windows MCP.
    """
    if PILOT not in surface:
        return surface
    text = query or ""
    external_followup = bool(
        getattr(surface[PILOT], "has_recent_target", False)
        and re.search(r"\bscroll\w*\b", text, re.I)
        and not _JARVIS_NAMED.search(text)
    )
    external_pointer_request = (
        bool(_POINTER_WORDS.search(text)) and not _JARVIS_NAMED.search(text)
    )
    external_request = names_other_program(text) or external_pointer_request or external_followup
    keep_browser = bool(_SAYS_BROWSER_SERVER.search(text))
    browser_skill_request = (
        OWN_BROWSER_HANDS in surface
        and wants_browser_skill(text)
        and not _CHROME_LAUNCH_CLICK_BEFORE_NAVIGATION.search(text)
        and not keep_browser
        and not wants_browser_window_control(text)
        and not _SAYS_SCREEN_HANDS.search(text)
        and not _SAYS_APP_HANDS.search(text)
        and not re.search(r"\b(?:mcp|server)\b", text, re.I)
    )
    said = set(re.findall(r"[a-z0-9]+", text.casefold()))

    def named(server: str) -> bool:
        words = set(re.findall(r"[a-z0-9]+", server.casefold())) - {"mcp", "server"}
        return bool(words and words <= said)

    def wrong_media_plugin(name: str) -> bool:
        """Keep website playback on the browser instead of a different service."""
        if name == "spotify":
            return bool(_SAYS_YOUTUBE.search(text)) and not _SAYS_SPOTIFY.search(text)
        if name == "youtube_music":
            return bool(_SAYS_YOUTUBE.search(text)) and not re.search(
                r"\byoutube\s+music\b", text, re.I,
            )
        return False

    servers: dict[str, set[str]] = {}
    for name, tool in surface.items():
        server = server_of(name, tool)
        if server:
            servers.setdefault(server, set()).add(name.rsplit("/", 1)[-1])
    pointer = {
        srv for srv, tails in servers.items()
        if srv == _RAW_APP_SERVER or {"Snapshot", "Click"} <= tails
    }

    if browser_skill_request:
        return {
            name: tool
            for name, tool in surface.items()
            if name not in {
                SCREEN_HANDS, APP_HANDS, "screen_snapshot", "window_control",
            }
            and not name.replace("-", "_").startswith("app_ui_")
            and not _is_browser_tool(name)
            and server_of(name, tool) not in pointer
            and not wrong_media_plugin(name)
        }

    if _CHROME_LAUNCH_CLICK_BEFORE_NAVIGATION.search(text):
        return {
            name: tool
            for name, tool in surface.items()
            if name not in {
                SCREEN_HANDS, OWN_BROWSER_HANDS, APP_HANDS, "screen_snapshot",
                "window_control", "open_app",
            }
            and not name.replace("-", "_").startswith("app_ui_")
            and not _is_browser_tool(name)
            and server_of(name, tool) not in pointer
            and not wrong_media_plugin(name)
        }

    def extra(name: str, tool: Tool) -> bool:
        if name == SCREEN_HANDS:
            return not _SAYS_SCREEN_HANDS.search(text)
        if name == OWN_BROWSER_HANDS:
            return not (
                _SAYS_MY_CHROME.search(text) or wants_own_browser(text)
            )
        if name == APP_HANDS:
            return not _SAYS_APP_HANDS.search(text)
        if name in {"spotify", "youtube_music"}:
            return wrong_media_plugin(name)
        if name.replace("-", "_").startswith("app_ui_"):
            return external_request
        if name == "compare_prices" or _is_browser_tool(name):
            return not keep_browser
        server = server_of(name, tool)
        if server in pointer and not named(server):
            return server == _RAW_APP_SERVER or name.rsplit("/", 1)[-1] in _POINTER_TAILS
        return False

    return {n: t for n, t in surface.items() if not extra(n, t)}


#: The one-call background app driver (desktop_driver.py) and the raw server it
#: replaces on the opening surface: Gemma copied example ids out of the raw
#: CUA tools' descriptions and typed into the wrong control (live 2026-10-25).
APP_HANDS = "app_control"
_RAW_APP_SERVER = "cua-driver"
#: Words that name a desktop app job.
_APP_WORDS = re.compile(
    r"\b(app|apps|application|program|window|calculator|calc|notepad|paint|wordpad|"
    r"settings|file\s+explorer|task\s+manager|button|buttons|press)\b",
    re.IGNORECASE,
)

#: Tools the request names directly, best first.
_RELEVANT_LIMIT = 10
#: Siblings added per MCP server a relevant tool belongs to.
_SIBLINGS_PER_SERVER = 6
#: Tools one ``enable_tools`` call may switch on.
_ENABLE_LIMIT = 8

Ranker = Callable[[dict[str, Tool], str, int], list[str]]


def server_of(name: str, tool: Any) -> str:
    """The MCP server a tool comes from, or ``""`` for a built-in."""
    if not getattr(tool, "is_mcp_tool", False):
        return ""
    return name.split("/", 1)[0] if "/" in name else ""


def _is_browser_tool(name: str) -> bool:
    tail = name.rsplit("/", 1)[-1]
    return tail.startswith("browser_") or tail in {"get_browser_state", "society_browser"}


def _one_line(tool: Any) -> str:
    text = " ".join(str(getattr(tool, "description", "") or "").split())
    if text.startswith("[ACTION-ONLY"):
        text = text.split("]", 1)[-1].strip()
    return text[:140]


class ToolSwitch:
    """Which of a turn's registered tools the model can currently see."""

    def __init__(self, tools: dict[str, Tool], rank: Ranker) -> None:
        self._tools = tools
        self._rank = rank
        self._on: dict[str, None] = {}
        #: Bumped on every change, so the tool loop knows to resend schemas.
        self.version = 0

    # -- reading -------------------------------------------------------------
    def is_on(self, name: str) -> bool:
        return name in self._on

    def visible(self) -> dict[str, Tool]:
        """The tools on the wire this round, in registry order."""
        return {n: t for n, t in self._tools.items() if n in self._on}

    def hidden_count(self) -> int:
        return sum(1 for n in self._tools if n not in self._on)

    # -- switching -----------------------------------------------------------
    def switch_on(self, names: Iterable[str]) -> list[str]:
        """Switch registered tools on; returns the ones that were off."""
        added = [n for n in dict.fromkeys(names) if n in self._tools and n not in self._on]
        for name in added:
            self._on[name] = None
        if added:
            self.version += 1
        return added

    def switch_off(self, names: Iterable[str]) -> None:
        """Hide tools again (the context fit dropped them); they stay callable."""
        gone = [n for n in names if n in self._on and n != ENABLE_TOOLS]
        for name in gone:
            del self._on[name]
        if gone:
            self.version += 1

    def handoff_to_pc_pilot(self) -> bool:
        """Replace a disconnected owner-browser tool with the desktop pilot.

        Keep the pilot registered but hidden on the first browser round. This
        is the deliberate one-driver fallback when BrowserSkill has no active
        Chrome profile; expose only the pilot for the recovery round so a
        small model cannot choose another browser stack.
        """
        if PILOT not in self._tools:
            return False
        replacement = {PILOT: None}
        if self._on != replacement:
            self._on = replacement
            self.version += 1
        return True

    def open_turn(
        self, query: str, *, core: Iterable[str] = DEFAULT_CORE, keep: Iterable[str] = ()
    ) -> list[str]:
        """The turn's opening surface: core, the request's own tools, gates' picks."""
        relevant = self._rank(self._tools, query, _RELEVANT_LIMIT) if query else []
        relevant = [
            n for n in relevant if n not in _ONLY_WHEN_SAID or _ONLY_WHEN_SAID[n].search(query)
        ]
        pinned = pinned_for_request(self._tools, query)
        relevant = [*pinned, *[n for n in relevant if n not in pinned]]
        if PILOT in self._tools:
            return self._open_pilot_turn(query, relevant, core=core, keep=keep)
        if (
            OWN_BROWSER_HANDS in self._tools
            and wants_browser_skill(query)
            and not wants_browser_window_control(query)
            and not _SAYS_SCREEN_HANDS.search(query or "")
            and not _SAYS_APP_HANDS.search(query or "")
            and not re.search(r"\b(?:mcp|server)\b", query or "", re.I)
        ):
            # A Chrome task belongs to the BrowserSkill adapter. Sending the
            # full core and ranked tools beside it let small local models pick
            # search or a Jarvis-panel control instead of the requested tab.
            self.switch_off(tuple(self._on))
            self.switch_on((ENABLE_TOOLS, OWN_BROWSER_HANDS))
            return [OWN_BROWSER_HANDS]
        if SCREEN_HANDS in self._tools and wants_browser_window_control(query or ""):
            # Browser servers own separate windows. Keep an explicit same-window
            # task on the observed desktop, including requests without a site URL.
            relevant = [
                n for n in relevant
                if n not in (OWN_BROWSER_HANDS, APP_HANDS) and not _is_browser_tool(n)
            ]
            self.switch_off([
                n for n in self._on
                if n == OWN_BROWSER_HANDS or _is_browser_tool(n)
            ])
            allowed = [
                n for n in (*core, *keep)
                if n != OWN_BROWSER_HANDS and not _is_browser_tool(n)
            ]
            self.switch_on([ENABLE_TOOLS, *allowed, SCREEN_HANDS, *relevant])
            return relevant
        hands = self._browser_hands(query, relevant)
        if OWN_BROWSER_HANDS in self._tools and (
            wants_own_browser(query) or _SAYS_CHROME.search(query or "")
        ):
            hands = [OWN_BROWSER_HANDS, *hands]
        if hands and _WEB_WORDS.search(query) and not wants_own_browser(query):
            # With both on, Gemma picked screen clicking for a web page (live
            # 2026-10-25) and the browser tools never ran. The manager keeps
            # computer_use whenever it is registered, so it leaves keep too.
            relevant = [n for n in relevant if n != SCREEN_HANDS]
            keep = [n for n in keep if n != SCREEN_HANDS]
        if APP_HANDS in self._tools:
            relevant = [n for n in relevant if not self._is_raw_app_tool(n)]
            if not hands and _APP_WORDS.search(query or ""):
                # Asked to use CUA by name, Gemma still chose computer_use,
                # which fronted Calculator and then hit Notepad's foreground
                # lock (live 2026-10-25). enable_tools can still bring it back.
                hands = [APP_HANDS]
                relevant = [n for n in relevant if n != SCREEN_HANDS]
                keep = [n for n in keep if n != SCREEN_HANDS]
        named = self._named_servers(query or "")
        if (
            not hands
            and _POINTER_WORDS.search(query or "")
            and names_other_program(query or "")
        ):
            # Live 2026-10-26 "open Windows PC settings, click on Windows
            # Update": with no desktop click tool on, Gemma clicked with
            # app_ui_click, which only reaches the Jarvis window.
            named = [*named, *self._desktop_pointer()]
        camera = [CAMERA] if wants_camera(query or "") else []
        self.switch_on(
            [ENABLE_TOOLS, *core, *keep, *camera, *hands, *relevant, *named,
             *self._siblings(query, relevant)]
        )
        return relevant

    def _open_pilot_turn(
        self, query: str, relevant: list[str], *, core: Iterable[str], keep: Iterable[str]
    ) -> list[str]:
        """The opening surface when the PC pilot is here: it is the desktop's hands.

        Screen clicking, the CUA app driver, the raw desktop servers' pointer
        tools and the browser servers leave the opening surface; the pilot
        opens a site in the owner's own browser and works the page by name.
        A request that names a browser server or a price comparison keeps those
        browser tools; an explicit request for the owner's Chrome keeps its
        dedicated BrowserSkill tool. Tools removed before this switch is built
        cannot be brought back by ``enable_tools``.
        """
        browser = bool(_SAYS_BROWSER_SERVER.search(query or ""))
        own_browser = bool(
            _SAYS_MY_CHROME.search(query or "")
            or wants_own_browser(query or "")
            or _SAYS_CHROME.search(query or "")
        )
        screen_requested = bool(_SAYS_SCREEN_HANDS.search(query or ""))
        app_requested = bool(_SAYS_APP_HANDS.search(query or ""))
        desktop_servers = {
            server_of(n, t) for n, t in self._tools.items() if self._is_pointer_server(n, t)
        }

        pilot_tool = self._tools[PILOT]
        external_followup = bool(
            getattr(pilot_tool, "has_recent_target", False)
            and re.search(r"\bscroll\w*\b", query or "", re.I)
            and not _JARVIS_NAMED.search(query or "")
        )
        named_server = bool(re.search(r"\b(?:mcp|server)\b", query or "", re.I))
        profile_selection = bool(
            _CHROME_LAUNCH_CLICK_BEFORE_NAVIGATION.search(query or "")
        )
        if profile_selection and PILOT in self._tools:
            # The owner profile chooser is a desktop UI, not a browser tab.
            # Use only the pilot until the target profile is selected; exposing
            # browser navigation here can make the model claim it navigated
            # while Chrome is still waiting at its chooser.
            self.switch_on((ENABLE_TOOLS, PILOT))
            return [PILOT]

        focus_desktop = (
            not browser
            and not own_browser
            and not screen_requested
            and not app_requested
            and not named_server
            and (
                (names_other_program(query) and _EXTERNAL_ACTION.search(query or ""))
                or (
                    bool(_POINTER_WORDS.search(query or ""))
                    and not _JARVIS_NAMED.search(query or "")
                )
                or external_followup
            )
        )

        if (
            own_browser
            and OWN_BROWSER_HANDS in self._tools
            and not screen_requested
            and not app_requested
            and not browser
            and not named_server
            and not wants_browser_window_control(query or "")
            and not profile_selection
        ):
            # Keep the focused first pass: the BrowserSkill handles the owner's
            # profile and tabs better than exposing overlapping drivers. The
            # PC pilot remains registered but hidden for a disconnected-browser
            # handoff in ToolUseLoop.
            self.switch_on((ENABLE_TOOLS, OWN_BROWSER_HANDS))
            return [OWN_BROWSER_HANDS]

        def other_hands(name: str) -> bool:
            tool = self._tools.get(name)
            if tool is None:
                return False
            if name == PILOT:
                return screen_requested or (
                    own_browser and OWN_BROWSER_HANDS in self._tools
                )
            if name == SCREEN_HANDS:
                return not screen_requested
            if name == APP_HANDS:
                return not app_requested
            if server_of(name, tool) in desktop_servers:
                return True
            if name == OWN_BROWSER_HANDS:
                return not own_browser
            return not browser and _is_browser_tool(name)

        if focus_desktop or screen_requested:
            # A named desktop action needs one driver. The default core plus
            # ranked MCP matches had put 22 schemas on Gemma for Chrome profile
            # selection, including Gmail, calendar, marketplace and memory.
            # Keep only the chosen driver, a gate-mandated tool, and the
            # switch itself so expansion stays deliberate.
            driver = SCREEN_HANDS if screen_requested else PILOT
            mandated = [
                name for name in keep
                if name in self._tools and name != driver and not other_hands(name)
            ]
            camera = [CAMERA] if wants_camera(query or "") else []
            self.switch_on([ENABLE_TOOLS, driver, *mandated, *camera])
            return [driver, *mandated]

        relevant = [n for n in relevant if not other_hands(n)]
        if own_browser and OWN_BROWSER_HANDS in self._tools:
            relevant = [
                OWN_BROWSER_HANDS,
                *(n for n in relevant if n != OWN_BROWSER_HANDS),
            ]
        named_hands = [
            name
            for name, pattern in (
                (SCREEN_HANDS, _SAYS_SCREEN_HANDS),
                (APP_HANDS, _SAYS_APP_HANDS),
            )
            if name in self._tools and pattern.search(query or "")
        ]
        named = self._named_servers(query or "")
        hands = self._browser_hands(query, relevant) if browser else []
        camera = [CAMERA] if wants_camera(query or "") else []
        siblings = [n for n in self._siblings(query, relevant) if not other_hands(n)]
        primary_hands = (
            [OWN_BROWSER_HANDS]
            if own_browser and OWN_BROWSER_HANDS in self._tools
            else [PILOT]
        )
        self.switch_on(
            [ENABLE_TOOLS, *primary_hands, *core,
             *[n for n in keep if not other_hands(n)], *camera,
             *named_hands,
             *hands, *relevant, *named, *siblings]
        )
        return relevant

    def _is_pointer_server(self, name: str, tool: Any) -> bool:
        """A tool of an MCP server that clicks in windows (Windows-MCP, CUA)."""
        server = server_of(name, tool)
        if not server:
            return False
        if server == _RAW_APP_SERVER:
            return True
        tails = {
            n.rsplit("/", 1)[-1] for n, t in self._tools.items() if server_of(n, t) == server
        }
        return "Snapshot" in tails and "Click" in tails

    def _named_servers(self, query: str) -> list[str]:
        """Tools of an MCP server the request names ("use Windows MCP").

        Live 2026-10-26 "USE WINDOWS MCP" got "I am not seeing a Windows MCP
        server configured" while windows-desktop was connected, its tools
        switched off.
        """
        said = set(re.findall(r"[a-z0-9]+", query.casefold()))
        if not said or not ({"mcp", "server"} & said or "tools" in said):
            return []
        out: list[str] = []
        servers = {server_of(n, t) for n, t in self._tools.items()} - {""}
        for server in sorted(servers):
            words = set(re.findall(r"[a-z0-9]+", server.casefold())) - {"mcp", "server"}
            if not words or not (words & said):
                continue
            members = {n: t for n, t in self._tools.items() if server_of(n, t) == server}
            ranked = self._rank(members, query, _SIBLINGS_PER_SERVER)
            pointer = [n for n in self._desktop_pointer() if n in members]
            out.extend(dict.fromkeys([*pointer, *ranked]))
        return out

    def _desktop_pointer(self) -> list[str]:
        """Snapshot, Click, Type and Scroll of a server that has Snapshot and Click."""
        by_server: dict[str, dict[str, str]] = {}
        for name, tool in self._tools.items():
            server = server_of(name, tool)
            if server and server != _RAW_APP_SERVER:
                by_server.setdefault(server, {})[name.rsplit("/", 1)[-1]] = name
        for server in sorted(by_server):
            tails = by_server[server]
            if "Snapshot" in tails and "Click" in tails:
                return [tails[t] for t in _DESKTOP_POINTER if t in tails]
        return []

    def enable_for(self, request: str, names: Iterable[str] = ()) -> list[str]:
        """Switch on what ``request`` describes, plus any exact ``names``."""
        exact = [n for n in names if n in self._tools]
        hidden = {n: t for n, t in self._tools.items() if n not in self._on}
        ranked = self._rank(hidden, request, _ENABLE_LIMIT) if request and hidden else []
        all_hands = self._browser_hands(request, ranked)
        if APP_HANDS in self._tools or all_hands:
            # "enable_tools: control Chrome browser" switched on the raw CUA
            # browser tools; Gemma made up pid 12345 and target_1 for them and
            # every call failed (live 2026-10-26). They need a DevTools
            # binding first, so the other browser server's tools do the job.
            ranked = [
                n
                for n in ranked
                if not self._is_raw_app_tool(n)
                or (APP_HANDS not in self._tools and not _is_browser_tool(n))
            ]
        hands = [n for n in all_hands if n in hidden]
        if hands and _WEB_WORDS.search(request) and not wants_own_browser(request):
            ranked = [n for n in ranked if n != SCREEN_HANDS]
        found = list(dict.fromkeys([*exact, *hands, *ranked, *self._siblings(request, ranked)]))
        return self.switch_on(found[: _ENABLE_LIMIT + len(exact) + len(hands)])

    def _browser_hands(self, query: str, relevant: list[str]) -> list[str]:
        """Navigate, read, click and type for a web job, from every browser server.

        A web job is a request in web words, or one whose matches include a
        ``browser_*`` tool.
        """
        web = bool(query and _WEB_WORDS.search(query)) or any(
            n.rsplit("/", 1)[-1].startswith("browser_") for n in relevant
        )
        if not web:
            return []
        # The one-call store walk (price_compare.py) comes first when it is here.
        own = [n for n in ("compare_prices",) if n in self._tools and _WEB_WORDS.search(query)]
        # CUA Driver's browser tools need a DevTools endpoint prepared first;
        # the Playwright server's open a page directly.
        return own + [
            name
            for hand in BROWSER_HANDS
            for name, tool in self._tools.items()
            if server_of(name, tool) not in ("", _RAW_APP_SERVER)
            and name.rsplit("/", 1)[-1] == hand
        ]

    def browser_hands_on(self, request: str) -> list[str]:
        """The browser tools already visible for a web job ``request``."""
        return [n for n in self._browser_hands(request, []) if n in self._on]

    def _is_raw_app_tool(self, name: str) -> bool:
        tool = self._tools.get(name)
        return tool is not None and server_of(name, tool) == _RAW_APP_SERVER

    def _siblings(self, query: str, relevant: list[str]) -> list[str]:
        """A relevant MCP tool brings its server's best-matching companions."""
        servers = {server_of(n, self._tools[n]) for n in relevant if n in self._tools} - {""}
        out: list[str] = []
        for server in sorted(servers):
            members = {
                n: t for n, t in self._tools.items() if server_of(n, t) == server
            }
            ranked = self._rank(members, query, _SIBLINGS_PER_SERVER) if query else []
            for name in members:
                if len(ranked) >= _SIBLINGS_PER_SERVER:
                    break
                if name not in ranked:
                    ranked.append(name)
            out.extend(ranked[:_SIBLINGS_PER_SERVER])
        return out

    def describe(self, names: Iterable[str]) -> str:
        return "\n".join(f"- {n}: {_one_line(self._tools[n])}" for n in names if n in self._tools)


@dataclass
class EnableToolsTool:
    """The switch the model flips when a job needs hands it cannot see."""

    switch: ToolSwitch
    name: str = ENABLE_TOOLS
    risk_tier: RiskTier = "safe"
    description: str = (
        "Switch on more tools for this task. You see a few tools; many more exist "
        "(browser, files, desktop control, mail, calendar, music, God's Eye, "
        "connected MCP servers). Describe what you need to do in plain words, or "
        "give exact tool names. The tools it lists are callable from your next step."
    )
    schema: dict[str, Any] | None = None
    #: Only hands back a list of tools, so it is no evidence that anything happened.
    yields_instructions_only: bool = True

    def __post_init__(self) -> None:
        if self.schema is None:
            self.schema = {
                "type": "object",
                "properties": {
                    "need": {
                        "type": "string",
                        "description": "The job in a few words, e.g. 'click a button in Chrome'.",
                    },
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Exact tool names, when you know them.",
                    },
                },
                "required": ["need"],
            }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        del ctx
        need = str(args.get("need") or "").strip()
        raw_names = args.get("names") or []
        names = [str(n) for n in raw_names] if isinstance(raw_names, list) else []
        ready = self.switch.browser_hands_on(need)
        added = self.switch.enable_for(need, names)
        # The browser tools the model already had answer a browser ask; say so,
        # or it keeps looking for others (live 2026-10-26).
        already = (
            "These browser tools are already on; call them now:\n" + self.switch.describe(ready)
            if ready
            else ""
        )
        if not added and already:
            return ToolResult(success=True, output=already)
        if not added:
            return ToolResult(
                success=True,
                output=(
                    "No further tool matches that. Use the tools you already have, "
                    "or say plainly that this cannot be done here."
                ),
            )
        log.info("tool switch: %r switched on %s", need[:80], ", ".join(added))
        return ToolResult(
            success=True,
            output="\n".join(
                filter(
                    None, [already, "Switched on, callable now:\n" + self.switch.describe(added)]
                )
            ),
        )


def wants_switch(mode: str, *, tool_count: int, context_window: int, local: bool) -> bool:
    """Whether a turn runs behind the switch.

    ``on`` and ``off`` are absolute. ``auto`` switches for a local model or a
    window of 128k or less once the registry outgrows what a turn needs; a
    frontier cloud window with a short list is left as it was.
    """
    if mode == "off":
        return False
    if mode == "on":
        return tool_count > 0
    if tool_count <= len(DEFAULT_CORE) + _RELEVANT_LIMIT:
        return False
    return local or 0 < context_window <= 131_072
