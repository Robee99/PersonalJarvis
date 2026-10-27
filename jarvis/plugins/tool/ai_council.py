"""ai_council tool: ChatGPT and Gemini debate a topic, Claude judges, in the owner's Chrome.

No API keys and no paid calls: the debate runs in the owner's own signed-in
chatgpt.com, gemini.google.com and claude.ai tabs, driven by the Jarvis AI
Council extension (``jarvis/assets/ai-council``, from Local AI Council by
valentinfomin, MIT). This tool only hands the topic over: it opens
``https://chatgpt.com/#jarvis-council=<topic>`` in Chrome, the extension's
script on that page opens the council and starts it. The verdict and the
whole debate are saved as a note in ``Downloads/AI Chats/Council``, which
``wiki/chat_sync`` imports into memory.

``sync_chats`` opens the Kept connect page with ``sync=1``, so the Kept
extension sends every new ChatGPT, Claude and Gemini chat to Jarvis memory now
instead of at its next hourly sync (``wiki/kept_bridge``).

Risk tier: monitor. It sends the topic as messages from the owner's own
accounts, which he asked for by naming the council.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

from jarvis.core.protocols import ExecutionContext, RiskTier, ToolResult

log = logging.getLogger(__name__)

AI_COUNCIL = "ai_council"
_JUDGES = ("claude", "chatgpt", "gemini", "grok")
_MAX_TOPIC = 2000
_KEPT_SYNC_URL = "http://127.0.0.1:18241/connect?sync=1&close=1"


def council_url(topic: str, judge: str = "claude") -> str:
    """The address that starts a council on ``topic`` with ``judge`` deciding."""
    fragment = urllib.parse.urlencode({"jarvis-council": topic[:_MAX_TOPIC], "judge": judge})
    return f"https://chatgpt.com/#{fragment}"


def chrome_path() -> str | None:
    """Chrome's executable on this PC, or ``None`` (the default browser is used)."""
    found = shutil.which("chrome") or shutil.which("google-chrome")
    if found:
        return found
    for base in (
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ):
        candidate = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if base and candidate.is_file():
            return str(candidate)
    return None


def open_in_chrome(url: str) -> None:
    """Open ``url`` in the owner's running Chrome (his profile, his sign-ins)."""
    exe = chrome_path()
    if exe:
        subprocess.Popen([exe, url], shell=False)  # noqa: S603 - fixed program, a URL argument
        return
    if hasattr(os, "startfile"):
        os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606 - the owner's own request
        return
    raise OSError("No browser to open here.")


class AiCouncilTool:
    """Start a ChatGPT vs Gemini debate judged by Claude, or sync chats into memory."""

    name: str = AI_COUNCIL
    risk_tier: RiskTier = "monitor"
    description: str = (
        "AI council in the owner's signed-in Chrome, free, no API keys: ChatGPT and Gemini "
        "debate a topic (answer, then critique each other) and Claude judges, naming the "
        "winner and giving the final answer. The verdict is saved to memory. Use for "
        "'ask the council', 'let ChatGPT and Gemini fight about X', 'debate this'. "
        "action=sync_chats pulls new ChatGPT/Claude/Gemini chats into memory now."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["debate", "sync_chats"], "default": "debate"},
            "topic": {"type": "string", "description": "The question or topic to debate."},
            "judge": {
                "type": "string",
                "enum": list(_JUDGES),
                "default": "claude",
                "description": "Who gives the final verdict; Claude unless the owner says.",
            },
        },
        "additionalProperties": False,
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        del ctx
        args = args or {}
        action = str(args.get("action") or "debate")
        try:
            if action == "sync_chats":
                open_in_chrome(_KEPT_SYNC_URL)
                return ToolResult(
                    success=True,
                    output=(
                        "Asked the Kept extension in Chrome to sync. New ChatGPT, Claude and "
                        "Gemini chats arrive in memory as it goes. If Chrome says the page "
                        "is not connected, the Kept extension is not loaded yet."
                    ),
                )
            topic = " ".join(str(args.get("topic") or "").split())
            if not topic:
                return ToolResult(
                    success=False, output=None, error="Say what the council should debate."
                )
            judge = str(args.get("judge") or "claude").lower()
            if judge not in _JUDGES:
                judge = "claude"
            open_in_chrome(council_url(topic, judge))
        except OSError as exc:  # Return browser launch failures in the tool result for the user.
            return ToolResult(success=False, output=None, error=f"Could not open Chrome: {exc}")
        return ToolResult(
            success=True,
            output=(
                f"The council is starting in Chrome: ChatGPT and Gemini debate, {judge.title()} "
                "judges. It takes a few minutes; the verdict is saved to memory under AI Chats/"
                "Council. If nothing starts, the Jarvis AI Council extension is not loaded in "
                "Chrome yet."
            ),
        )
