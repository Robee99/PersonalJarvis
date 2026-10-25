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
#: Pixel-level desktop control, left off for a web job the browser can do.
SCREEN_HANDS = "computer_use"
#: Words that mean the job happens on a web page.
_WEB_WORDS = re.compile(
    r"\b(browser|website|web\s*site|web\s*page|webpage|online|shop\w*|store|stores|"
    r"price\w*|cheap\w*|afford\w*|grocer\w*|order|checkout|form|forms|url|"
    r"chrome|edge|firefox)\b|https?://|\.(com|in|org|net|io)\b",
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
        hands = self._browser_hands(query, relevant)
        if hands and _WEB_WORDS.search(query):
            # With both on, Gemma picked screen clicking for a web page (live
            # 2026-10-25) and the browser tools never ran.
            relevant = [n for n in relevant if n != SCREEN_HANDS]
        self.switch_on(
            [ENABLE_TOOLS, *core, *keep, *hands, *relevant, *self._siblings(query, relevant)]
        )
        return relevant

    def enable_for(self, request: str, names: Iterable[str] = ()) -> list[str]:
        """Switch on what ``request`` describes, plus any exact ``names``."""
        exact = [n for n in names if n in self._tools]
        hidden = {n: t for n, t in self._tools.items() if n not in self._on}
        ranked = self._rank(hidden, request, _ENABLE_LIMIT) if request and hidden else []
        hands = [n for n in self._browser_hands(request, ranked) if n in hidden]
        if hands and _WEB_WORDS.search(request):
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
        return [
            name
            for hand in BROWSER_HANDS
            for name, tool in self._tools.items()
            if server_of(name, tool) and name.rsplit("/", 1)[-1] == hand
        ]

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
        added = self.switch.enable_for(need, names)
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
            output="Switched on, callable now:\n" + self.switch.describe(added),
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
