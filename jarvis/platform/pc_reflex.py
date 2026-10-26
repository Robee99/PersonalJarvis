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
from typing import Any

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
#: "the volume", "my computer's volume", "the PC sound". The battery ask "Set my
#: computer's volume to 35 percent" missed the reflex, and Gemma answered
#: "Setting it now" with no call (live 2026-10-25).
_VOL = (
    r"(?:(?:the|my)\s+)?(?:(?:system|master|computer|pc|laptop)(?:'s|s)?\s+)?"
    r"(?:volume|sound)"
)
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


#: Joins between orders in one breath: "unmute and set the volume to 50".
_V_JOIN = re.compile(r"\s*(?:,\s*)?\b(?:and\s+then|and|then)\b\s*|\s*,\s*", re.IGNORECASE)


def parse_volume_commands(text: str) -> list[VolumeCommand]:
    """Every volume order in ``text``, in order, or ``[]``.

    "Unmute and set the volume to 50" is two plain orders; left to the brain it
    was answered "Unmuted, 50%" with nothing changed (2026-10-20). The whole
    utterance must be volume orders: one other clause and the brain takes it.
    """
    single = parse_volume_command(text)
    if single is not None:
        return [single]
    parts = [p for p in _V_JOIN.split(text or "") if p and p.strip()]
    if len(parts) < 2:
        return []
    commands = [parse_volume_command(part) for part in parts]
    return [c for c in commands if c is not None] if all(commands) else []


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
    """The reply for plain volume commands on Windows, else ``None``.

    Several orders run in turn; the reply is the last read-back, which states
    the final level and mute state.
    """
    commands = parse_volume_commands(text)
    if not commands or sys.platform != "win32":
        return None
    from jarvis.plugins.tool.system_volume import VolumeUnavailable, apply  # noqa: PLC0415

    try:
        reply = ""
        for command in commands:
            reply = apply(command.action, command.percent)
        return reply
    except (VolumeUnavailable, OSError, ImportError) as exc:
        log.warning("volume reflex failed: %s", exc)
        return "I couldn't reach Windows' volume control, so I can't confirm any change."


#: Resizing a window that is not open has one honest answer. Live 2026-10-25,
#: "Maximize the Notepad window" with no Notepad open went to Gemma, which
#: answered about notes. "close downloads" or "focus chat" may mean a tab or a
#: folder, so those still go to the brain.
_NOT_OPEN_IS_THE_ANSWER = frozenset({"maximize", "minimize", "restore"})


def _spoken(window: str) -> str:
    return window.title() if len(window) > 3 else window.upper()


def window_reflex(text: str) -> str | None:
    """The reply for a plain window command on Windows, else ``None``.

    A window that cannot be found returns ``None`` for close and focus: "close
    the downloads" may mean a browser tab or a folder, and the brain can look.
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
    except LookupError:  # no such window: said for resizes, else the brain looks
        if command.action in _NOT_OPEN_IS_THE_ANSWER:
            return f"I can't find an open {_spoken(command.window)} window, so nothing changed."
        return None
    except ValueError:  # no window named: the brain asks which one
        return None
    except (OSError, RuntimeError) as exc:
        log.warning("window reflex failed: %s", exc)
        return None
    name = _spoken(command.window)
    if command.action == "close" and result.state != "closed":
        return f"I asked {name} to close; it is still open, it may be asking to save."
    if command.action == "focus" and result.state != "minimized":
        return f"{name} is in front."
    return f"{name} is {_STATE_WORDS.get(result.state, result.state)}."


_W_LIST = re.compile(
    rf"^\s*{_PLEASE}(?:(?:which|what)\s+(?:apps|programs|applications|windows)"
    r"(?:\s+and\s+(?:apps|programs|applications|windows))?\s+(?:are|do\s+i\s+have)\s+"
    r"(?:open|running)|list\s+(?:the\s+|my\s+|all\s+)?(?:open\s+)?(?:apps|windows|programs)"
    r"|(?:show|tell)\s+me\s+(?:the\s+|my\s+)?open\s+(?:apps|windows|programs))"
    r"(?:\s+(?:on\s+(?:my|the)\s+(?:desktop|screen|computer|pc)|right\s+now|now))*"
    r"[?.!\s]*$",
    re.IGNORECASE,
)


def window_list_reflex(text: str) -> str | None:
    """The open windows, read from Windows, for a plain "which windows are open?".

    Live 2026-10-25 Gemma answered "Which apps and windows are open on my
    desktop right now?" with "I don't keep a history of your activity" while
    window_control's list was one call away.
    """
    if not _W_LIST.match(text or "") or sys.platform != "win32":
        return None
    from jarvis.plugins.tool.window_control import apply  # noqa: PLC0415

    try:
        return apply("list", "")
    except (OSError, RuntimeError) as exc:
        log.warning("window list reflex failed: %s", exc)
        return None


_FACT_ASK = (
    r"^\s*(?:(?:hey\s+)?jarvis[,\s]+)?"
    r"(?:how\s+much|what(?:'s|\s+is)|how's|check|tell\s+me)\b"
)
_DISK_ASK = re.compile(
    _FACT_ASK + r"[^?]{0,40}\b(?:free|available|left)\b[^?]{0,20}\b(?:space|storage)\b"
    r"|" + _FACT_ASK + r"[^?]{0,40}\b(?:space|storage)\b[^?]{0,30}\b(?:free|left|available)\b",
    re.IGNORECASE,
)
_DRIVE = re.compile(r"\b([a-z])\s*(?::|\s+drive\b)", re.IGNORECASE)
_BATTERY_ASK = re.compile(
    _FACT_ASK + r"[^?]{0,30}\bbattery\b(?:\s+(?:level|percentage|percent|charge|life|status))?"
    r"\s*(?:at|now|right\s+now)?\s*[?.!]*\s*$",
    re.IGNORECASE,
)


def _gb(n: int) -> str:
    return f"{n / 1024**3:.0f} GB" if n >= 10 * 1024**3 else f"{n / 1024**3:.1f} GB"


def system_facts_reflex(text: str) -> str | None:
    """Free disk space and battery level, read from the machine.

    Live 2026-10-25 Gemma answered "How much free space is on my C drive?"
    with "I do not have a registered tool", and nothing answered the battery.
    Both are one read; only a plain question about either matches.
    """
    if len((text or "").split()) > 14:
        return None
    if _DISK_ASK.search(text or ""):
        import os  # noqa: PLC0415
        import shutil  # noqa: PLC0415

        m = _DRIVE.search(text)
        if m:
            root = f"{m.group(1).upper()}:\\" if sys.platform == "win32" else "/"
        else:
            root = os.environ.get("SystemDrive", "C:") + "\\" if sys.platform == "win32" else "/"
        try:
            usage = shutil.disk_usage(root)
        except OSError as exc:
            log.warning("disk reflex: %s unreadable: %s", root, exc)
            return f"I couldn't read the {root} drive."
        name = f"the {root.rstrip(chr(92))} drive" if sys.platform == "win32" else "the disk"
        return f"{_gb(usage.free)} free of {_gb(usage.total)} on {name}."
    if _BATTERY_ASK.search(text or ""):
        try:
            import psutil  # noqa: PLC0415

            battery = psutil.sensors_battery()
        except (ImportError, OSError, RuntimeError) as exc:
            log.warning("battery reflex failed: %s", exc)
            return None
        if battery is None:
            return "This computer reports no battery."
        state = "charging" if battery.power_plugged else "on battery"
        return f"The battery is at {battery.percent:.0f} percent, {state}."
    return None


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


_MEDIA_NOUN = (
    r"(?:(?:the|my|this|that)\s+)?(?:music|song|track|playback|video|player|"
    r"youtube(?:\s+music)?|spotify)"
)
_MEDIA_THING = rf"(?:{_MEDIA_NOUN}|it)"
_M_PAUSE = re.compile(
    rf"^\s*{_PLEASE}(?:pause(?:\s+{_MEDIA_THING})?|(?:stop|halt)\s+{_MEDIA_NOUN}){_TAIL}",
    re.IGNORECASE,
)
_M_PLAY = re.compile(
    rf"^\s*{_PLEASE}(?:resume|unpause)(?:\s+{_MEDIA_THING})?{_TAIL}"
    rf"|^\s*{_PLEASE}continue\s+{_MEDIA_THING}{_TAIL}"
    rf"|^\s*{_PLEASE}(?:play|start)\s+{_MEDIA_THING}\s+again{_TAIL}",
    re.IGNORECASE,
)
_M_NEXT = re.compile(
    rf"^\s*{_PLEASE}(?:(?:play\s+)?(?:the\s+)?next\s+(?:song|track)|"
    rf"skip\s+(?:to\s+the\s+next\s+(?:song|track)|{_MEDIA_NOUN})){_TAIL}",
    re.IGNORECASE,
)
_M_PREV = re.compile(
    rf"^\s*{_PLEASE}(?:(?:play\s+)?(?:the\s+)?(?:previous|last)\s+(?:song|track)|"
    rf"go\s+back\s+(?:a|one)\s+(?:song|track)){_TAIL}",
    re.IGNORECASE,
)


def parse_media_command(text: str) -> str | None:
    """The player command ("pause", "play", "next", "previous") this utterance
    plainly orders, else ``None``. "Stop", "next" and "skip" need a media word:
    alone they may mean a timer, a slide or a step."""
    if not _plain(text):
        return None
    for action, pattern in (
        ("pause", _M_PAUSE), ("play", _M_PLAY), ("next", _M_NEXT), ("previous", _M_PREV)
    ):
        if pattern.match(text):
            return action
    return None


_MEDIA_DONE = {"pause": "Paused", "play": "Resumed", "next": "Skipped to", "previous": "Back to"}
_MEDIA_STATUS = {"pause": "paused", "play": "playing"}


def media_reflex(text: str, *, controller: Any | None = None) -> str | None:
    """Pause, resume or skip whatever plays, through the OS media session.

    Live 2026-10-25 "Pause the music" matched the Spotify skill, which was not
    connected, while YouTube Music played in Chrome; nothing paused. Every
    player that answers the media keys (YouTube Music, Spotify, a YouTube tab)
    is in the OS media session, so this needs no connected account. The reply
    is what the session reads back afterwards. ``controller`` is injectable
    for tests (``jarvis.platform.media_session.MediaSessionController``).
    """
    action = parse_media_command(text)
    if action is None or (controller is None and sys.platform != "win32"):
        return None
    import asyncio  # noqa: PLC0415

    if controller is None:
        from jarvis.platform.media_session import make_media_session_controller  # noqa: PLC0415

        controller = make_media_session_controller()

    async def _run() -> str | None:
        if not (await controller.capability()).can_read:
            # Media keys only: nothing can be read back, so say what was pressed.
            if not await getattr(controller, action)():
                return None
            key = "play/pause" if action in _MEDIA_STATUS else action
            return f"I pressed the {key} media key; I can't read back what is playing."
        before = await controller.now_playing()
        if before is None:
            if action == "play":
                return None  # nothing to resume: the brain may open a player
            return "Nothing is playing, so there is nothing to " + (
                "pause." if action == "pause" else "skip."
            )
        if not await getattr(controller, action)():
            return f"The player did not accept {action}, so nothing changed."
        await asyncio.sleep(0.4)  # the session reports the new state a moment later
        after = await controller.now_playing()
        if after is None:
            return f"I sent {action} to the player, but it no longer reports what plays."
        title = after.title or "the track"
        if action in _MEDIA_STATUS and after.status != _MEDIA_STATUS[action]:
            return f"I sent {action} to {after.app}, but it still reads {after.status}."
        return f"{_MEDIA_DONE[action]} {title} in {after.app}."

    try:
        return asyncio.run(_run())
    except (OSError, RuntimeError) as exc:
        log.warning("media reflex failed: %s", exc)
        return None
