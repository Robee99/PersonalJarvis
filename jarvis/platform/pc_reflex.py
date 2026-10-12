"""Volume and window commands as voice reflexes that only claim what Windows reads back.

"Volume 40", "mute", "maximize Notepad", "bring Chrome back", "open youtube.com"
are deterministic,
low-risk commands, so Jarvis answers them without a model round trip, also
when Hermes is the brain, the same way it answers brightness
(``jarvis/platform/brightness.py``). The work is done by the same code the
``system_volume`` and ``window_control`` tools run for Hermes, and the reply
is the state read back afterwards.

Why a reflex and not only the tools: live on the PC (2026-10-12 01:12), Gemma
under Hermes kept "maximizing" Notepad with the Windows App tool's resize and
said "Notepad has been maximized" while the window stayed normal, even with
window_control offered and named in SOUL.md. Each of those turns also cost
5-100 s of model time for a one-word action.

A named web address opens in the default browser (the same as typing it into
Run). Live 2026-10-12 06:30: "Open YouTube dot com" went to Hermes, Gemma
called Jarvis's legacy screen tool, which had no vision model, and then said
"I am opening YouTube for you now" while nothing opened.

Only plain imperatives match. A negation, a question, a condition, a second
clause ("unmute and open Spotify"), an app's own volume ("Spotify volume to
30") or a window that cannot be found stays with the brain. Windows only.
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)

_NEGATION = re.compile(
    r"\b(don'?t|do\s+not|never|not|no\s+need|stop\s+changing|leave)\b", re.IGNORECASE
)
_QUESTION = re.compile(
    r"\?|^\s*(how|why|what|when|where|which|who|is|are|does|do|did|should|could|would|"
    r"can\s+you\s+(tell|explain|show)\s+me)\b",
    re.IGNORECASE,
)
_CONDITION = re.compile(
    r"\b(if|when|unless|until|after|before|later|tomorrow|and|then|also)\b", re.IGNORECASE
)
_PLEASE = r"(?:(?:please|jarvis|hey\s+jarvis|ok(?:ay)?|can\s+you|could\s+you)[,\s]+)*"
_TAIL = r"(?:[,\s]+(?:please|thanks?|thank\s+you|jarvis|now))*[.!\s]*$"
_VOL = r"(?:the\s+)?(?:system\s+|master\s+|computer\s+|pc\s+|laptop\s+)?(?:volume|sound)"
_PCT = r"(\d{1,3})\s*(?:%|percent|per\s+cent)?"

#: How far "louder" / "volume up" move without a number (matches system_volume).
VOLUME_STEP = 10


@dataclass(frozen=True)
class VolumeCommand:
    """One plain volume order: a system_volume action and its percent."""

    action: str
    percent: int | None = None


_V_SET = re.compile(
    rf"^\s*{_PLEASE}(?:(?:set|change|put|turn|make)\s+{_VOL}\s+(?:to\s+|at\s+)?|"
    rf"{_VOL}\s+(?:to\s+|at\s+)?){_PCT}{_TAIL}",
    re.IGNORECASE,
)
_V_STEP = re.compile(
    rf"^\s*{_PLEASE}(?:(?:turn|bring|crank)\s+{_VOL}\s+(up|down)|(?:turn|bring|crank)\s+(up|down)\s+{_VOL}|"
    rf"{_VOL}\s+(up|down)|(increase|raise|decrease|lower|reduce)\s+{_VOL})"
    rf"(?:\s+by\s+{_PCT})?{_TAIL}",
    re.IGNORECASE,
)
_V_WORD = re.compile(
    rf"^\s*{_PLEASE}(?:make\s+it\s+|a\s+bit\s+)?(louder|quieter|softer){_TAIL}", re.IGNORECASE
)
_V_MUTE = re.compile(
    rf"^\s*{_PLEASE}(mute|unmute)(?:\s+(?:{_VOL}|(?:the\s+)?(?:audio|speakers?|computer|pc|laptop)))?{_TAIL}",
    re.IGNORECASE,
)


def _plain(text: str) -> bool:
    return bool(text) and not (
        _NEGATION.search(text) or _QUESTION.search(text) or _CONDITION.search(text)
    )


def parse_volume_command(text: str) -> VolumeCommand | None:
    """The master-volume change this utterance plainly orders, else ``None``."""
    if not _plain(text):
        return None
    m = _V_SET.match(text)
    if m:
        return VolumeCommand("set", max(0, min(100, int(m.group(1)))))
    m = _V_STEP.match(text)
    if m:
        word = next(g for g in m.groups()[:4] if g).lower()
        up = word in ("up", "increase", "raise")
        amount = int(m.group(5)) if m.group(5) else VOLUME_STEP
        return VolumeCommand("up" if up else "down", amount)
    m = _V_WORD.match(text)
    if m:
        return VolumeCommand("up" if m.group(1).lower() == "louder" else "down", VOLUME_STEP)
    m = _V_MUTE.match(text)
    if m:
        return VolumeCommand(m.group(1).lower())
    return None


@dataclass(frozen=True)
class WindowCommand:
    """One plain window order: a window_control action and the window named."""

    action: str
    window: str


_W_VERBS = {
    "minimize": "minimize", "minimise": "minimize", "hide": "minimize",
    "maximize": "maximize", "maximise": "maximize",
    "restore": "restore", "unminimize": "restore", "unminimise": "restore",
    "focus": "focus", "close": "close",
}
_W_TARGET = r"(?:the\s+)?([a-z0-9][a-z0-9 .+\-]{1,30}?)(?:\s+(?:window|app|application))?"
_W_ACTION = re.compile(
    rf"^\s*{_PLEASE}({'|'.join(_W_VERBS)})\s+{_W_TARGET}{_TAIL}", re.IGNORECASE
)
_W_BRING = re.compile(
    rf"^\s*{_PLEASE}(?:bring\s+(?:back\s+|up\s+)?{_W_TARGET}(?:\s+(?:back|up))?(?:\s+to\s+the\s+front)?|"
    rf"(?:switch|go)\s+(?:back\s+)?to\s+{_W_TARGET}|show\s+(?:me\s+)?{_W_TARGET}\s+again)"
    rf"{_TAIL}",
    re.IGNORECASE,
)
#: Words that name no window by themselves; "close it" or "close the tab" goes to the brain.
_VAGUE = frozenset({
    "it", "this", "that", "them", "everything", "all", "all windows", "window", "windows",
    "the window", "app", "tab", "this tab", "the tab", "screen", "desktop", "everything else",
    "music", "video", "song", "page", "door", "volume", "sound",
})


def parse_window_command(text: str) -> WindowCommand | None:
    """The window change this utterance plainly orders, else ``None``."""
    if not _plain(text):
        return None
    m = _W_ACTION.match(text)
    if m:
        action, target = _W_VERBS[m.group(1).lower()], m.group(2)
    else:
        m = _W_BRING.match(text)
        if not m:
            return None
        action, target = "focus", next(g for g in m.groups() if g)
    target = target.strip().lower()
    if target in _VAGUE or len(target.split()) > 3:
        return None
    return WindowCommand(action, target)


_STATE_WORDS = {
    "minimized": "minimized", "maximized": "maximized", "normal": "open", "closed": "closed",
}


def volume_reflex(text: str) -> str | None:
    """The reply for a plain volume command on Windows, else ``None``."""
    command = parse_volume_command(text)
    if command is None or sys.platform != "win32":
        return None
    from jarvis.plugins.tool.system_volume import VolumeUnavailable, apply  # noqa: PLC0415

    try:
        return apply(command.action, command.percent)
    except (VolumeUnavailable, OSError, ImportError) as exc:
        log.warning("volume reflex failed: %s", exc)
        return "I couldn't reach Windows' volume control, so I can't confirm any change."


def window_reflex(text: str) -> str | None:
    """The reply for a plain window command on Windows, else ``None``.

    A window that cannot be found returns ``None``: "close the downloads" may
    mean a browser tab or a folder, and the brain can look.
    """
    command = parse_window_command(text)
    if command is None or sys.platform != "win32":
        return None
    from jarvis.plugins.tool.window_control import change  # noqa: PLC0415

    try:
        # Closing by title text could close a browser whose tab says "chat";
        # close only ever matches the program itself.
        result = change(
            command.action, command.window, program_only=command.action == "close"
        )
    except (LookupError, ValueError):
        return None
    except (OSError, RuntimeError) as exc:
        log.warning("window reflex failed: %s", exc)
        return None
    name = command.window.title() if len(command.window) > 3 else command.window.upper()
    if command.action == "close" and result.state != "closed":
        return f"I asked {name} to close; it is still open, it may be asking to save."
    if command.action == "focus" and result.state != "minimized":
        return f"{name} is in front."
    return f"{name} is {_STATE_WORDS.get(result.state, result.state)}."


_TLD = r"(?:com|org|net|io|in|co|dev|ai|app|gg|tv|me|de|es|uk)"
_DOMAIN = rf"((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+{_TLD})(/[^\s]*)?"
_URL_OPEN = re.compile(
    rf"^\s*{_PLEASE}(?:open|go\s+to|launch|visit|load|pull\s+up|bring\s+up)\s+"
    rf"(?:up\s+)?(?:the\s+)?(?:website\s+|site\s+|page\s+)?(?:https?://)?(?:www\.)?{_DOMAIN}"
    rf"(?:\s+(?:in\s+(?:my\s+|the\s+)?(?:browser|chrome)))?{_TAIL}",
    re.IGNORECASE,
)


def parse_url_command(text: str) -> str | None:
    """The web address this utterance plainly asks to open, as an https URL, else ``None``."""
    from jarvis.brain.local_action_gate import _spoken_command  # noqa: PLC0415

    command = _spoken_command((text or "").strip())
    if not _plain(command):
        return None
    m = _URL_OPEN.match(command)
    if not m:
        return None
    return f"https://{m.group(1).lower()}{m.group(2) or ''}"


def url_reflex(text: str) -> str | None:
    """Open a plainly named web address in the default browser on Windows, else ``None``."""
    url = parse_url_command(text)
    if url is None or sys.platform != "win32":
        return None
    import os  # noqa: PLC0415

    try:
        os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606 - a fixed https URL, opened by the browser
    except OSError as exc:
        log.warning("url reflex failed: %s", exc)
        return None
    site = url.removeprefix("https://").rstrip("/")
    return f"Opened {site} in your browser."
