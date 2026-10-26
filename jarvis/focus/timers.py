"""Timers and reminders, kept by Jarvis itself.

Live 2026-10-26 "Set a timer for 2 minutes" ended on "the command never ran"
and "Remind me in 10 minutes to drink water" on "Noted, I will remind you",
with nothing set: Jarvis had no timer of its own, and the local model could
not drive a background shell process for one. This module is that timer.

* **Asks.** "set a timer for 2 minutes", "a 10 minute timer", "remind me in
  10 minutes to drink water", "remind me to call mum at 5:30 pm", "cancel the
  timer", "how long is left on the timer", "what reminders do I have". Only
  plain asks match; anything else goes to the brain.
* **Ringing.** When one is due Jarvis says it (an announcement on the bus,
  spoken when a voice session is open) and shows a Windows notification, so a
  timer set from the typed chat still rings with no call open.
* **Restarts.** Every pending entry is kept in ``timers.json`` in the user
  data folder. The next start re-arms them, and one that fell due while
  Jarvis was closed rings at once and says when it was due.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

Notify = Callable[[str], Awaitable[None]]

#: The longest timer or reminder, in seconds (one week).
MAX_SECONDS = 7 * 24 * 3600

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "twenty five": 25, "twenty-five": 25, "thirty": 30,
    "forty": 40, "forty five": 45, "forty-five": 45, "fifty": 50, "sixty": 60,
    "ninety": 90, "half an": 0.5, "a half": 0.5,
}
_UNIT_SECONDS = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600}
_AMOUNT = r"(?:\d{1,4}(?:\.\d+)?|half\s+an|a\s+half|[a-z]+(?:[\s-][a-z]+)?)"
_UNIT = r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)"
_DURATION = rf"(?P<dur>{_AMOUNT}\s*{_UNIT}(?:\s+(?:and\s+)?{_AMOUNT}\s*{_UNIT})?)"
_CLOCK = r"(?P<clock>\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)|\d{1,2}:\d{2})"
_POLITE = re.compile(
    r"^(?:(?:hey\s+)?jarvis[,\s]+|please\s+|can\s+you\s+|could\s+you\s+|ok(?:ay)?[,\s]+)+"
)
_TAIL = re.compile(r"(?:[,\s]+(?:please|thanks?|thank\s+you|jarvis|sir))*[.!?\s]*$")

_TIMER = (
    re.compile(rf"^(?:set|start|put\s+on|make)\s+(?:a\s+|an\s+|the\s+)?timer\s+(?:for\s+)?{_DURATION}$"),
    re.compile(rf"^(?:set|start|put\s+on|make)\s+(?:a\s+|an\s+)?{_DURATION}\s+timer$"),
    re.compile(rf"^(?:a\s+)?timer\s+(?:for\s+)?{_DURATION}$"),
    re.compile(rf"^(?:a\s+)?{_DURATION}\s+timer$"),
    re.compile(rf"^(?:set\s+an?\s+)?alarm\s+(?:for\s+|in\s+)?{_DURATION}(?:\s+from\s+now)?$"),
)
_REMIND = (
    re.compile(rf"^remind\s+me\s+in\s+{_DURATION}$"),
    re.compile(rf"^remind\s+me\s+in\s+{_DURATION}\s+(?:to|that|about)\s+(?P<what>.+)$"),
    re.compile(rf"^remind\s+me\s+(?:to|that|about)\s+(?P<what>.+?)\s+in\s+{_DURATION}$"),
    re.compile(rf"^remind\s+me\s+at\s+{_CLOCK}\s+(?:to|that|about)\s+(?P<what>.+)$"),
    re.compile(rf"^remind\s+me\s+(?:to|that|about)\s+(?P<what>.+?)\s+at\s+{_CLOCK}$"),
    re.compile(rf"^(?:set\s+a\s+)?reminder\s+(?:for\s+|in\s+)?{_DURATION}\s+(?:to|for|about)\s+(?P<what>.+)$"),
)
_CANCEL = re.compile(
    r"^(?:cancel|stop|delete|remove|clear|turn\s+off)\s+(?:the\s+|my\s+|all\s+(?:the\s+|my\s+)?)?"
    r"(?P<kind>timers?|reminders?|alarms?)$"
)
_STATUS = re.compile(
    r"^(?:how\s+(?:much\s+time|long)\s+(?:is\s+)?left\s+on\s+(?:the\s+|my\s+)?timers?"
    r"|(?:what|which)\s+(?:timers|reminders|alarms)\s+(?:do\s+i\s+have|are\s+(?:set|running|on))"
    r"|(?:list|show)\s+(?:me\s+)?(?:my\s+|the\s+)?(?:timers|reminders|alarms)"
    r"|(?:any|are\s+there\s+any)\s+(?:timers|reminders)(?:\s+(?:set|running))?)$"
)


def _amount(word: str) -> float | None:
    word = " ".join(word.lower().split())
    if re.fullmatch(r"\d+(?:\.\d+)?", word):
        return float(word)
    return _NUMBER_WORDS.get(word)


def parse_duration(text: str) -> int | None:
    """Seconds in "2 minutes", "an hour and 30 minutes", "ninety seconds"."""
    total = 0.0
    found = False
    for amount, unit in re.findall(rf"({_AMOUNT})\s*({_UNIT})", text.lower()):
        amount = re.sub(r"^and\s+", "", amount.strip())
        value = _amount(amount)
        if value is None:
            return None
        key = next(k for k in _UNIT_SECONDS if unit.startswith(k))
        total += value * _UNIT_SECONDS[key]
        found = True
    seconds = int(round(total))
    return seconds if found and 1 <= seconds <= MAX_SECONDS else None


def parse_clock(text: str, now: datetime) -> datetime | None:
    """The next moment the clock reads "5:30 pm" / "17:30" / "9 am", from ``now``."""
    m = re.fullmatch(
        r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?", text.strip().lower()
    )
    if not m:
        return None
    hour, minute, half = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half.startswith("p") else 0)
    if hour > 23 or minute > 59:
        return None
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return due if due > now else due + timedelta(days=1)


def spoken_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [
        f"{n} {unit}{'s' if n != 1 else ''}"
        for n, unit in ((hours, "hour"), (minutes, "minute"), (secs, "second"))
        if n
    ]
    return " and ".join(parts) if parts else "0 seconds"


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0")


@dataclass
class Entry:
    """One pending timer or reminder."""

    id: str
    kind: str  # "timer" or "reminder"
    due: float  # epoch seconds
    label: str = ""
    seconds: int = 0  # the length a timer was set for


def _toast(title: str, body: str) -> None:
    """A Windows notification through PowerShell's WinRT bridge (no extra package)."""
    if sys.platform != "win32":
        return
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: PLC0415

    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("'", "''")
        )

    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] | Out-Null; "
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
        "ContentType = WindowsRuntime] | Out-Null; "
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument; "
        f"$x.LoadXml('<toast scenario=\"reminder\"><visual><binding template=\"ToastGeneric\">"
        f"<text>{esc(title)}</text><text>{esc(body)}</text></binding></visual>"
        "<actions><action content=\"Dismiss\" arguments=\"dismiss\" activationType=\"system\"/>"
        "</actions><audio src=\"ms-winsoundevent:Notification.Looping.Alarm\"/></toast>'); "
        "$t = [Windows.UI.Notifications.ToastNotification]::new($x); "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'"
        ").Show($t)"
    )
    subprocess.run(  # noqa: S603 - fixed program; the text is escaped above
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, timeout=15, check=False, creationflags=NO_WINDOW_CREATIONFLAGS,
    )


class TimerBoard:
    """Every pending timer and reminder, each with one sleeping task."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
        toast: Callable[[str, str], None] | None = _toast,
    ) -> None:
        self._path = path
        self._clock = clock
        self._toast = toast
        self._entries: dict[str, Entry] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._notify: Notify | None = None
        self._honorific = "Sir"
        self._loaded = False

    # -- persistence ---------------------------------------------------------

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = [asdict(e) for e in self._entries.values()]
            self._path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError as exc:
            log.warning("timers: could not save %s: %s", self._path, exc)

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self._path is None or not self._path.exists():
            return
        try:
            rows = json.loads(self._path.read_text(encoding="utf-8"))
            for row in rows:
                entry = Entry(**row)
                self._entries[entry.id] = entry
        except (OSError, ValueError, TypeError) as exc:
            log.warning("timers: %s unreadable, starting empty: %s", self._path, exc)

    # -- running -------------------------------------------------------------

    def bind(self, notify: Notify | None, honorific: str | None = None) -> None:
        """Set how a due entry is said, and re-arm what a restart left pending."""
        if notify is not None:
            self._notify = notify
        if honorific is not None:
            self._honorific = honorific.strip()
        self._load()
        for entry in list(self._entries.values()):
            if entry.id not in self._tasks:
                self._arm(entry)

    def _arm(self, entry: Entry) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # armed on the next bind from the event loop
        self._tasks[entry.id] = loop.create_task(self._wait(entry), name=f"timer-{entry.id}")

    async def _wait(self, entry: Entry) -> None:
        late = self._clock() - entry.due
        delay = max(0.0, -late)
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self._entries.pop(entry.id, None)
        self._tasks.pop(entry.id, None)
        self._save()
        await self._ring(entry, missed=late > 60)

    def ring_text(self, entry: Entry, *, missed: bool = False) -> str:
        if entry.kind == "reminder" and not entry.label:
            text = "this is the reminder you asked for."
        elif entry.kind == "reminder":
            text = f"a reminder: {entry.label}."
        else:
            text = f"your {spoken_duration(entry.seconds)} timer is done."
        text = f"{self._honorific}, {text}" if self._honorific else text[0].upper() + text[1:]
        if missed:
            text += f" It was due at {_clock(entry.due)}, while I was closed."
        return text

    async def _ring(self, entry: Entry, *, missed: bool) -> None:
        text = self.ring_text(entry, missed=missed)
        log.info("timers: %s %s due", entry.kind, entry.id)
        if self._toast is not None:
            title = "Reminder" if entry.kind == "reminder" else "Timer done"
            try:
                await asyncio.to_thread(self._toast, f"Jarvis: {title}", text)
            except (OSError, subprocess.SubprocessError) as exc:
                log.warning("timers: notification failed: %s", exc)
        if self._notify is not None:
            try:
                await self._notify(text)
            except Exception as exc:  # noqa: BLE001 — a speech fault must not lose the ring
                log.warning("timers: announcement failed: %s", exc)

    # -- commands ------------------------------------------------------------

    def add(self, kind: str, seconds: float, label: str = "") -> Entry:
        self._load()
        entry = Entry(
            id=uuid.uuid4().hex[:8], kind=kind, due=self._clock() + seconds,
            label=label, seconds=int(round(seconds)),
        )
        self._entries[entry.id] = entry
        self._save()
        self._arm(entry)
        return entry

    def pending(self, kind: str | None = None) -> list[Entry]:
        self._load()
        rows = [e for e in self._entries.values() if kind is None or e.kind == kind]
        return sorted(rows, key=lambda e: e.due)

    def cancel(self, kind: str) -> int:
        gone = self.pending(kind)
        for entry in gone:
            self._entries.pop(entry.id, None)
            task = self._tasks.pop(entry.id, None)
            if task is not None:
                task.cancel()
        self._save()
        return len(gone)


_BOARD: TimerBoard | None = None


def get_board() -> TimerBoard:
    global _BOARD
    if _BOARD is None:
        from jarvis.core.paths import user_data_dir  # noqa: PLC0415

        _BOARD = TimerBoard(user_data_dir() / "data" / "timers.json")
    return _BOARD


def _normalise(text: str) -> str:
    phrase = " ".join((text or "").strip().lower().split())
    phrase = _POLITE.sub("", phrase)
    return _TAIL.sub("", phrase).strip()


def _status(board: TimerBoard) -> str:
    rows = board.pending()
    if not rows:
        return "There are no timers or reminders set."
    now = board._clock()
    parts = []
    for entry in rows[:5]:
        left = spoken_duration(max(0, entry.due - now))
        if entry.kind == "reminder":
            what = f" to {entry.label}" if entry.label else ""
            parts.append(f"a reminder{what} at {_clock(entry.due)}")
        else:
            parts.append(f"a {spoken_duration(entry.seconds)} timer with {left} left")
    return "You have " + "; ".join(parts) + "."


async def timer_reflex(
    text: str,
    *,
    board: TimerBoard | None = None,
    notify: Notify | None = None,
    honorific: str | None = None,
    now: datetime | None = None,
) -> str | None:
    """Set, list or cancel a timer or reminder without a model, else ``None``."""
    phrase = _normalise(text)
    if not phrase or not re.search(r"\b(?:timers?|remind(?:er|ers)?|alarms?)\b", phrase):
        return None
    if re.search(r"\b(?:don'?t|do\s+not|never)\b", phrase):
        return None
    board = board or get_board()
    board.bind(notify, honorific)
    m = _CANCEL.match(phrase)
    if m:
        kind = "reminder" if m.group("kind").startswith("remind") else "timer"
        count = board.cancel(kind)
        if not count:
            return f"There was no {kind} set."
        return f"Cancelled {count} {kind}{'s' if count != 1 else ''}."
    if _STATUS.match(phrase):
        return _status(board)
    for pattern in _TIMER:
        m = pattern.match(phrase)
        if m:
            seconds = parse_duration(m.group("dur"))
            if seconds is None:
                return None
            entry = board.add("timer", seconds)
            return (
                f"Timer set for {spoken_duration(seconds)}. It rings at {_clock(entry.due)}."
            )
    for pattern in _REMIND:
        m = pattern.match(phrase)
        if not m:
            continue
        groups = m.groupdict()
        what = re.sub(r"^(?:to\s+)", "", groups.get("what") or "").strip(" .")
        if groups.get("dur"):
            seconds = parse_duration(groups["dur"])
        else:
            current = now or datetime.fromtimestamp(board._clock())
            due = parse_clock(groups["clock"], current)
            seconds = None if due is None else (due - current).total_seconds()
        if seconds is None or not 1 <= seconds <= MAX_SECONDS:
            return None
        entry = board.add("reminder", seconds, what)
        if not what:
            return f"I'll remind you at {_clock(entry.due)}."
        return f"I'll remind you to {what} at {_clock(entry.due)}."
    return None


__all__ = [
    "Entry",
    "TimerBoard",
    "get_board",
    "parse_clock",
    "parse_duration",
    "spoken_duration",
    "timer_reflex",
]
