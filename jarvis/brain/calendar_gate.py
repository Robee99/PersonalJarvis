"""An honest answer to a calendar ask while no calendar is connected.

Battery 2026-10-26: "What is on my calendar today?" ran a web search for
the words, because the google_calendar tool is only offered once Google
Calendar is connected. With neither Google Calendar nor Outlook connected,
a plain calendar ask now gets the one step that fixes it instead.
"""

from __future__ import annotations

import re

#: Catalog plugins that can read the owner's calendar.
CALENDAR_PLUGINS = ("google_calendar", "outlook")

_ASK = re.compile(
    r"\b(?:my\s+(?:calendar|schedule|agenda|meetings?|appointments?)"
    r"|(?:on|in|to|from|check|read)\s+(?:the\s+|my\s+)?calendar"
    r"|calendar\s+(?:today|tomorrow|this\s+week|for\s+(?:today|tomorrow))"
    r"|(?:any|what)\s+(?:meetings?|appointments?)\s+(?:today|tomorrow|this\s+week)"
    r"|do\s+i\s+have\s+(?:any\s+)?(?:meetings?|appointments?|events?)"
    r"|(?:aaj|kal)\s+(?:ki|ka|ke)\s+(?:meetings?|schedule|appointments?)"
    r"|calendar\s+(?:mein|me|main)\b)"
)
_HOW_TO = re.compile(r"\b(?:how\s+(?:do|does|can|to)|what\s+is\s+a|explain|app|google\s+search)\b")


def is_calendar_ask(text: str) -> bool:
    """True for a plain ask about the owner's own calendar."""
    phrase = " ".join((text or "").lower().split())
    if not phrase or len(phrase.split()) > 20 or _HOW_TO.search(phrase):
        return False
    return bool(_ASK.search(phrase))


def not_connected_reply(honorific: str = "Sir") -> str:
    address = f", {honorific}" if honorific else ""
    return (
        f"Your calendar isn't connected yet{address}. Open Plugins in Jarvis, pick "
        "Google Calendar and press Connect, then sign in with Google. After that I "
        "can read your day and add events."
    )


__all__ = ["CALENDAR_PLUGINS", "is_calendar_ask", "not_connected_reply"]
