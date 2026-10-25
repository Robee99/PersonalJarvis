"""Focus sessions: an accountability timer that knows which window you are in.

From the owner's prompt pack (2026-10-25, prompts 9-12): "thirty minutes on
this" by voice or the FOCUS button starts a session that lives on the
SERVER, watches the foreground window every few seconds, and calls a drift
out by name ("That is YouTube, Sir, not the report").

* **The target.** Started from another window, the session locks on that
  window at once. Started from Jarvis itself (the FOCUS button lives in the
  Jarvis window), locking "the active window" would lock Jarvis, so the lock
  is *deferred*: the first other window you stay in for two ticks becomes
  the target and the assistant says so. A deferred lock nobody mentions is
  the same as a broken one.
* **App or tab.** A browser target locks the tab (its page title); anything
  else locks the app. "Lock on this tab" moves the target to the window in
  front, or defers again when that is Jarvis.
* **Drift.** Two ticks off target is a drift. Jarvis's own window is neutral:
  talking to the assistant is never a drift. A callout names the distraction
  and has a cooldown, so a long detour is one remark, not a stream.
* **The eyes.** A head-down notice from the webcam (live_vision_routes) counts
  as a phone drift with its own lines.

Everything here is local: the foreground title is read through
``jarvis.platform.window_state`` and never leaves the process. ``diag()``
reports booleans only.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

log = logging.getLogger(__name__)

Say = Callable[[str], Awaitable[None]]
TitleReader = Callable[[], str]
Lock = Literal["app", "tab"]

#: Seconds between two looks at the foreground window.
TICK_S = 5.0
#: Ticks a window must hold before it counts (a lock, a drift).
HOLD_TICKS = 2
#: Seconds between two spoken callouts.
CALLOUT_COOLDOWN_S = 45.0
MAX_MINUTES = 240
DEFAULT_MINUTES = 30

_BROWSERS = (
    "google chrome",
    "chrome",
    "chromium",
    "microsoft edge",
    "edge",
    "mozilla firefox",
    "firefox",
    "brave",
    "opera",
    "vivaldi",
    "arc",
)
_OWN_MARKERS = ("jarvis", "j.a.r.v.i.s")
_SEPARATOR = re.compile(r"\s+[-–—|]\s+")

_DRIFT_LINES = (
    "{h}, that is {away}, not {target}. Back we go.",
    "{away} again, {h}. {target} is where the work is.",
    "A small detour into {away}, {h}. {target} awaits.",
)
_PHONE_LINES = (
    "Phone down, {h}. {target} is waiting.",
    "The phone can wait, {h}. {target} cannot, I am told.",
)


def _minutes(n: int) -> str:
    return "1 minute" if n == 1 else f"{n} minutes"


def _clean(title: str) -> str:
    return " ".join(title.replace("​", "").split())


def surface(title: str) -> tuple[str, str]:
    """``(app, page)`` from a window title: "Report - Google Docs - Google Chrome"."""
    parts = _SEPARATOR.split(_clean(title))
    if len(parts) == 1:
        return parts[0], ""
    return parts[-1], " - ".join(parts[:-1])


def is_browser(app: str) -> bool:
    return app.strip().lower() in _BROWSERS


def is_own(title: str) -> bool:
    lowered = title.lower()
    return any(marker in lowered for marker in _OWN_MARKERS)


_NUMBER_WORDS = {
    "five": 5,
    "ten": 10,
    "fifteen": 15,
    "twenty": 20,
    "twenty five": 25,
    "twenty-five": 25,
    "thirty": 30,
    "forty": 40,
    "forty five": 45,
    "forty-five": 45,
    "fifty": 50,
    "sixty": 60,
    "ninety": 90,
}


def parse_minutes(text: str) -> int | None:
    """Minutes from "25 minutes", "thirty minutes", "an hour", "half an hour"."""
    phrase = text.strip().lower()
    if re.fullmatch(r"half\s+an\s+hour", phrase):
        return 30
    if re.fullmatch(r"an?\s+hour", phrase):
        return 60
    if re.fullmatch(r"a\s+quarter\s+of\s+an\s+hour", phrase):
        return 15
    match = re.fullmatch(r"(\d{1,3}|[a-z]+(?:[\s-][a-z]+)?)\s+min(?:ute)?s?", phrase)
    if not match:
        return None
    amount = match.group(1)
    value = int(amount) if amount.isdigit() else _NUMBER_WORDS.get(amount)
    if value is None or not 1 <= value <= MAX_MINUTES:
        return None
    return value


@dataclass
class _Target:
    app: str
    page: str
    lock: Lock

    @property
    def name(self) -> str:
        return self.page if self.lock == "tab" and self.page else self.app

    def holds(self, app: str, page: str) -> bool:
        if app.lower() != self.app.lower():
            return False
        return self.lock == "app" or page == self.page


@dataclass
class FocusSession:
    """One focus session at a time; ``start`` replaces a running one."""

    read_title: TitleReader | None = None
    clock: Callable[[], float] = time.monotonic
    say: Say | None = None
    honorific: str = "Sir"
    active: bool = False
    goal: str = ""
    minutes: int = 0
    started_at: float = 0.0
    ends_at: float = 0.0
    drifts: int = 0
    phone_drifts: int = 0
    on_target_ticks: int = 0
    counted_ticks: int = 0
    _target: _Target | None = None
    _deferred_lock: Lock | None = None
    _candidate: tuple[str, str] | None = None
    _candidate_ticks: int = 0
    _away_ticks: int = 0
    _last_callout: float = float("-inf")
    _lines_used: int = 0
    _task: asyncio.Task[None] | None = field(default=None, repr=False)

    # -- wiring ----------------------------------------------------------------
    def bind(self, say: Say | None, honorific: str | None = None) -> None:
        if say is not None:
            self.say = say
        if honorific is not None:
            self.honorific = honorific

    def _title(self) -> str:
        reader = self.read_title
        if reader is None:
            from jarvis.platform.window_state import get_foreground_title

            reader = get_foreground_title
        return _clean(reader() or "")

    async def speak(self, text: str) -> None:
        if self.say is None:
            log.info("focus: no speaker bound; would say %r", text)
            return
        try:
            await self.say(text)
        except Exception:  # noqa: BLE001 — a lost callout must not end the session
            log.warning("focus: callout could not be spoken", exc_info=True)

    def _h(self) -> str:
        return self.honorific or "friend"

    # -- commands --------------------------------------------------------------
    def start(self, minutes: int = DEFAULT_MINUTES, goal: str = "") -> str:
        """Start (or restart) a session; returns the line to say."""
        minutes = max(1, min(MAX_MINUTES, int(minutes)))
        now = self.clock()
        self.active = True
        self.goal = goal.strip()[:120]
        self.minutes = minutes
        self.started_at = now
        self.ends_at = now + minutes * 60
        self.drifts = self.phone_drifts = self.on_target_ticks = self.counted_ticks = 0
        self._away_ticks = 0
        self._last_callout = float("-inf")
        title = self._title()
        if not title or is_own(title):
            self._target = None
            self._deferred_lock = "app"
            self._candidate, self._candidate_ticks = None, 0
            self._ensure_running()
            return (
                f"{_minutes(minutes)}, {self._h()}. Go to your work; "
                "I will lock on the first window you stay in."
            )
        self._lock_on(title)
        self._ensure_running()
        assert self._target is not None
        return f"{_minutes(minutes)} on {self._target.name}, {self._h()}. The clock is running."

    def lock_here(self) -> str:
        """Lock on the window in front ("lock on this tab")."""
        if not self.active:
            return f"There is no focus session running, {self._h()}."
        title = self._title()
        if not title or is_own(title):
            self._target = None
            self._deferred_lock = "tab"
            self._candidate, self._candidate_ticks = None, 0
            return "Go to the tab you want; I will lock on it once you stay there."
        self._lock_on(title, lock="tab")
        assert self._target is not None
        return f"Locked on {self._target.name}."

    def set_goal(self, goal: str) -> str:
        self.goal = goal.strip()[:120]
        return f"Noted: {self.goal}." if self.goal else "Goal cleared."

    def stop(self) -> str:
        if not self.active:
            return f"There is no focus session running, {self._h()}."
        report = self.report(finished=False)
        self._end()
        return report

    def note_phone(self) -> str | None:
        """A head-down notice from the webcam while a session runs."""
        if not self.active or self._target is None:
            return None
        self.drifts += 1
        self.phone_drifts += 1
        return self._callout(_PHONE_LINES, away="the phone")

    # -- the watch ---------------------------------------------------------------
    async def tick(self) -> None:
        if not self.active:
            return
        if self.clock() >= self.ends_at:
            report = self.report(finished=True)
            self._end()
            await self.speak(report)
            return
        title = self._title()
        if not title or is_own(title):
            self._candidate, self._candidate_ticks = None, 0
            return
        app, page = surface(title)
        if self._target is None:
            await self._tick_deferred(title, app, page)
            return
        self.counted_ticks += 1
        if self._target.holds(app, page):
            self.on_target_ticks += 1
            self._away_ticks = 0
            return
        self._away_ticks += 1
        if self._away_ticks == HOLD_TICKS:
            self.drifts += 1
            away = page if is_browser(app) and page else app
            line = self._callout(_DRIFT_LINES, away=away)
            if line:
                await self.speak(line)

    async def _tick_deferred(self, title: str, app: str, page: str) -> None:
        key = (app.lower(), page if self._deferred_lock == "tab" or is_browser(app) else "")
        if key == self._candidate:
            self._candidate_ticks += 1
        else:
            self._candidate, self._candidate_ticks = key, 1
        if self._candidate_ticks < HOLD_TICKS:
            return
        self._lock_on(title, lock=self._deferred_lock or "app")
        assert self._target is not None
        ask = "" if self.goal else " And what are we focusing on?"
        await self.speak(f"Locked on {self._target.name}, {self._h()}.{ask}")

    def _lock_on(self, title: str, lock: Lock | None = None) -> None:
        app, page = surface(title)
        if lock is None or lock == "app":
            lock = "tab" if is_browser(app) and page else "app"
        self._target = _Target(app=app, page=page, lock=lock)
        self._deferred_lock = None
        self._candidate, self._candidate_ticks = None, 0
        self._away_ticks = 0

    def _callout(self, lines: tuple[str, ...], *, away: str) -> str | None:
        now = self.clock()
        if now - self._last_callout < CALLOUT_COOLDOWN_S or self._target is None:
            return None
        self._last_callout = now
        line = lines[self._lines_used % len(lines)]
        self._lines_used += 1
        target = self.goal or self._target.name
        return line.format(h=self._h(), away=away, target=target)

    def _end(self) -> None:
        self.active = False
        self._target = None
        self._deferred_lock = None
        task, self._task = self._task, None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    def _ensure_running(self) -> None:
        if self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.warning("focus: no event loop; the session will tick only when asked")
            return
        self._task = loop.create_task(self._run(), name="focus-session")

    async def _run(self) -> None:
        while self.active:
            await asyncio.sleep(TICK_S)
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 — one bad look must not end the session
                log.exception("focus: tick failed")

    # -- reading ---------------------------------------------------------------
    def on_target_percent(self) -> int:
        if not self.counted_ticks:
            return 100
        return round(100 * self.on_target_ticks / self.counted_ticks)

    def report(self, *, finished: bool) -> str:
        spent = max(0, round((min(self.clock(), self.ends_at) - self.started_at) / 60))
        drifts = "no drifts" if not self.drifts else (
            "one drift" if self.drifts == 1 else f"{self.drifts} drifts"
        )
        head = (
            f"Time, {self._h()}. {_minutes(self.minutes)}"
            if finished
            else f"Focus ended after {spent} of {_minutes(self.minutes)}"
        )
        return f"{head}: on target {self.on_target_percent()} percent, {drifts}."

    def snapshot(self) -> dict[str, Any]:
        remaining = max(0, round(self.ends_at - self.clock())) if self.active else 0
        target = self._target
        return {
            "active": self.active,
            "goal": self.goal,
            "minutes": self.minutes,
            "remaining_s": remaining,
            "target": target.name if target else "",
            "lock": target.lock if target else (self._deferred_lock or ""),
            "deferred": self.active and target is None,
            "drifts": self.drifts,
            "phone_drifts": self.phone_drifts,
            "on_target_percent": self.on_target_percent(),
        }

    def diag(self) -> dict[str, bool]:
        """Booleans only: no window title or app name leaves this call."""
        title = self._title()
        app, _page = surface(title) if title else ("", "")
        return {
            "foreground_readable": bool(title),
            "foreground_is_browser": is_browser(app),
            "foreground_is_jarvis": bool(title) and is_own(title),
            "active": self.active,
            "deferred": self.active and self._target is None,
            "locked": self._target is not None,
            "speaker_bound": self.say is not None,
        }


_SESSION: FocusSession | None = None


def get_session() -> FocusSession:
    """The process's one focus session (the web routes and voice share it)."""
    global _SESSION
    if _SESSION is None:
        _SESSION = FocusSession()
    return _SESSION


# -- voice ---------------------------------------------------------------------

_POLITE = re.compile(r"^(?:(?:please|jarvis|hey\s+jarvis|ok(?:ay)?|can\s+you|could\s+you)[,\s]+)+")
_TAIL = re.compile(r"(?:[,\s]+(?:please|thanks?|thank\s+you|jarvis|sir|now))*[.!\s]*$")
_DUR = r"(?:\d{1,3}|[a-z]+(?:[\s-][a-z]+)?)\s+min(?:ute)?s?|half\s+an\s+hour|an?\s+hour"
_START_PATTERNS = (
    re.compile(rf"^(?P<dur>{_DUR})\s+on\s+(?P<goal>.+)$"),
    re.compile(
        rf"^(?:let'?s\s+)?(?:focus|work)\s+on\s+(?P<goal>.+?)\s+for\s+(?P<dur>{_DUR})$"
    ),
    re.compile(
        rf"^(?:let'?s\s+)?(?:start\s+)?(?:a\s+)?(?:focus|work)(?:\s+session|\s+mode)?"
        rf"(?:\s+on)?(?:\s+for\s+(?P<dur>{_DUR}))?(?:\s+on\s+(?P<goal>.+))?$"
    ),
)
_LOCK = re.compile(
    r"^lock\s+(?:on\s+|onto\s+|to\s+)?(?:this|the\s+current|current)\s+(?:tab|window|app|page)$"
)
_STOP = re.compile(
    r"^(?:(?:stop|end|cancel|finish)\s+(?:the\s+|my\s+)?focus(?:\s+session|\s+mode)?"
    r"|focus\s+(?:mode\s+)?off)$"
)
_STATUS = re.compile(r"^(?:focus\s+status|how\s+much\s+(?:focus\s+)?time\s+(?:is\s+)?left)\??$")
_BARE_START = re.compile(r"^(?:start\s+)?(?:a\s+)?focus(?:\s+session|\s+mode)?(?:\s+on)?$")
_KEYWORDS = ("focus", "minute", "hour", "lock", "work", "left")
_VAGUE_GOALS = {"this", "it", "that", "my work", "work", "the work"}


def _normalise(text: str) -> str:
    phrase = " ".join(text.strip().lower().split())
    phrase = _POLITE.sub("", phrase)
    return _TAIL.sub("", phrase).strip()


async def focus_reflex(
    text: str, *, session: FocusSession | None = None, say: Say | None = None,
    honorific: str | None = None,
) -> str | None:
    """Answer a focus command without a model, or ``None`` for anything else."""
    phrase = _normalise(text)
    if not phrase or not any(w in phrase for w in _KEYWORDS):
        return None
    focus = session or get_session()
    focus.bind(say, honorific)
    if _STOP.match(phrase):
        return focus.stop()
    if _LOCK.match(phrase):
        return focus.lock_here()
    if _STATUS.match(phrase):
        if not focus.active:
            return f"There is no focus session running, {focus._h()}."
        left = max(1, round(focus.snapshot()["remaining_s"] / 60))
        return f"{_minutes(left)} left, {focus.drifts} drifts so far."
    for pattern in _START_PATTERNS:
        match = pattern.match(phrase)
        if not match:
            continue
        dur = match.group("dur")
        minutes = parse_minutes(dur) if dur else DEFAULT_MINUTES
        if minutes is None:
            return None
        goal = (match.group("goal") or "").strip()
        if pattern is _START_PATTERNS[2] and not dur and not _BARE_START.match(phrase):
            # "work", "focus on Chrome": a window command or chat, not a session.
            return None
        return focus.start(minutes, "" if goal in _VAGUE_GOALS else goal)
    return None
