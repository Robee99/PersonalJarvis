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
    "open_app",
    "window_control",
    "system_volume",
    "screen_snapshot",
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
    r"open\s+tabs?)\b",
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


def wants_own_browser(text: str) -> bool:
    """Whether a request is about the person's own open browser, not a fresh one."""
    return bool(text and _OWN_BROWSER.search(text))


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
    return tail.startswith("browser_") or tail == "get_browser_state"


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

    def open_turn(
        self, query: str, *, core: Iterable[str] = DEFAULT_CORE, keep: Iterable[str] = ()
    ) -> list[str]:
        """The turn's opening surface: core, the request's own tools, gates' picks."""
        relevant = self._rank(self._tools, query, _RELEVANT_LIMIT) if query else []
        relevant = [
            n for n in relevant if n not in _ONLY_WHEN_SAID or _ONLY_WHEN_SAID[n].search(query)
        ]
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
        self.switch_on(
            [ENABLE_TOOLS, *core, *keep, *hands, *relevant, *self._siblings(query, relevant)]
        )
        return relevant

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
