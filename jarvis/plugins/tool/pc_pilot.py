"""pc_pilot: one tool that drives the whole PC the way a person's hands would.

Live 2026-10-26 Roberto asked Jarvis to open Windows Settings and click
Windows Update, to work Calculator and Chrome, and to look at his screen.
Gemma 4 E4B had five overlapping sets of hands (computer_use, app_control,
the raw Windows-MCP and CUA servers, app_ui_click) and failed in each a
different way: a Click with a worded label and no Snapshot first, made-up
window ids, clicks that landed in Jarvis's own window, and a guard that
read "type ..." as research.

The pilot does the bookkeeping a small model gets wrong:

* **One call, many steps.** ``steps`` runs open, click, type, keys, scroll
  and window actions in order. Each step finds its window itself, steps off
  Jarvis's own window, and brings the target to the front.
* **Controls by name.** It reads the window through UI Automation every
  time, so a click names what a person sees ("Windows Update", "Seven") and
  never a stale id. Spoken buttons map to Calculator's names.
* **Act, check, retry.** After each step it reads the window back. A
  control that is not there yet is looked for again once after the window
  settles; typed text is compared with what the field then holds. The model
  gets the window's controls back, so its next step starts from the truth.
* **Eyes on every step.** Each completed or failed desktop step returns a
  fresh screenshot through the privacy-filtered capture, alongside UIA
  readback. A vision-capable brain can ground the next action in the actual
  screen instead of guessing from labels alone.
* **A policy, checked before every step.** In the spirit of NVIDIA
  OpenShell's declared policies: ask first before a click or key that
  deletes, sends, pays, installs, signs out or shuts down, and before any
  typing into a password field or a credential window (the step must say
  ``secret``, which makes the call ask). Every step is written to an audit
  log, typed text by length only. ``pilot_policy.json`` in the data folder
  adds words or windows; it can only make the policy stricter.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import difflib
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.protocols import ExecutionContext, RiskTier, ToolResult
from jarvis.platform.win_pilot import (
    CLICKABLE_ROLES,
    Control,
    UIAQueryError,
    Window,
    key_codes,
)

log = logging.getLogger(__name__)

PILOT = "pc_pilot"

_MAX_STEPS = 1
_MAX_LISTED = 70
#: Controls that appeared after the last click or key, listed first.
_MAX_FRESH = 30
#: A window with fewer named controls than this goes back as a picture too.
_FEW_CONTROLS = 4
#: Seconds a freshly opened window gets to appear.
_OPEN_WAIT_S = 25.0
_SETTLE_S = 0.45
#: Seconds an opened window may take to finish drawing its controls.
_OPEN_SETTLE_S = 3.0
#: How far a click looks down a page for a name it cannot see, in scrolls.
_FIND_SCROLLS = 8
#: open_app's answer when no such program is on this PC.
_NOT_INSTALLED = re.compile(r"not (?:be )?found|not installed|no app", re.IGNORECASE)
#: Seconds a web page gets to replace the typed address with its own title.
_PAGE_WAIT_S = 8.0
#: Keys that open a shell surface (Start, search, Run, notifications, clipboard).
_SHELL_KEYS = frozenset({"win", "windows", "win+s", "win+r", "win+a", "win+n", "win+v", "win+."})
_ACTIONS = (
    "open", "navigate", "app", "click", "set", "drag", "hover", "type", "keys",
    "scroll", "window", "wait", "look",
)
_WINDOW_OPS = ("focus", "minimize", "maximize", "restore", "close")

# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

#: Click or key targets that change something for good: asked first.
_ASK_WORDS = (
    "delete", "remove", "uninstall", "erase", "wipe", "format", "reset", "send",
    "pay", "buy", "purchase", "order", "checkout", "place order", "subscribe",
    "transfer", "install", "shut down", "shutdown", "restart", "sign out",
    "log out", "logout", "empty recycle bin", "factory", "discard", "overwrite",
    "permanently", "submit", "post", "publish", "accept all",
)
#: Windows whose fields are credentials: typing there needs ``secret``.
_DENY_TYPING_WINDOWS = (
    r"windows security", r"credential", r"user account control", r"bitlocker", r"keepass",
    r"bitwarden", r"1password", r"lastpass",
)
#: Key combinations that need a yes: they close, lock or delete.
_ASK_KEYS = ("alt+f4", "shift+delete", "win+l", "ctrl+alt+delete", "ctrl+shift+esc")
_ENTER_KEYS = frozenset({"enter", "return"})
_SAFE_ENTER_TARGET = re.compile(r"\b(?:search|address|url|filter|find|query)\b", re.IGNORECASE)


#: What the model hears at a password field (advice text, not a password).
_SECRET_HINT = (
    "That is a password field. To type into it, call again with secret=true on that "  # noqa: S105
    "step; the owner is asked first and the text is never logged."
)


@dataclass
class PilotPolicy:
    ask_words: tuple[str, ...] = _ASK_WORDS
    deny_typing_windows: tuple[str, ...] = _DENY_TYPING_WINDOWS
    ask_keys: tuple[str, ...] = _ASK_KEYS
    #: Windows (title patterns) the pilot may not touch at all.
    deny_windows: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: Path | None) -> PilotPolicy:
        """Defaults plus the owner's additions; a file can only add rules."""
        policy = cls()
        if path is None or not path.is_file():
            return policy
        try:
            extra = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):  # a broken file keeps the defaults
            log.warning("pc_pilot: %s unreadable; default policy only", path)
            return policy
        for key in ("ask_words", "deny_typing_windows", "ask_keys", "deny_windows"):
            values = extra.get(key) if isinstance(extra, dict) else None
            if isinstance(values, list):
                merged = (*getattr(policy, key), *(str(v) for v in values if str(v).strip()))
                setattr(policy, key, tuple(dict.fromkeys(merged)))
        return policy

    def needs_yes(self, step: dict[str, Any]) -> str:
        """Why ``step`` needs the owner's yes, or ``""``."""
        if "click" in step:
            said = _norm(str(step["click"]))
            for word in self.ask_words:
                if re.search(rf"\b{re.escape(word)}\b", said):
                    return f"clicking {step['click']!r} ({word})"
        if "keys" in step:
            combo = str(step["keys"]).replace(" ", "").casefold()
            if combo in self.ask_keys:
                return f"pressing {step['keys']}"
        enters = bool(step.get("enter")) or (
            str(step.get("keys", "")).replace(" ", "").casefold() in _ENTER_KEYS
        )
        if enters:
            target = " ".join(str(step.get(key, "")) for key in ("into", "on", "app"))
            typed = str(step.get("type", ""))
            dangerous_text = any(
                re.search(rf"\b{re.escape(word)}\b", typed, re.I)
                for word in self.ask_words
            )
            safe_search = (
                "type" in step
                and bool(_SAFE_ENTER_TARGET.search(target))
                and not dangerous_text
            )
            if not safe_search:
                return "pressing Enter, which can submit or confirm an action"
        if step.get("window") == "close":
            return "closing a window"
        if "type" in step and step.get("secret"):
            return "typing a password or other secret"
        return ""

    def refusal(self, step: dict[str, Any], window: Window | None, field_is_password: bool) -> str:
        """Why ``step`` may not run at all, or ``""``."""
        title = (window.title if window else "").casefold()
        for pattern in self.deny_windows:
            if title and re.search(pattern, title, re.IGNORECASE):
                return f"The policy keeps the pilot out of '{window.title}'."  # type: ignore[union-attr]
        if "type" in step and not step.get("secret"):
            if field_is_password:
                return _SECRET_HINT
            for pattern in self.deny_typing_windows:
                if title and re.search(pattern, title, re.IGNORECASE):
                    return f"'{window.title}' asks for credentials. {_SECRET_HINT}"  # type: ignore[union-attr]
        return ""


def _audit(path: Path | None, entry: dict[str, Any]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:  # the audit trail is best effort; the step already ran
        log.debug("pc_pilot: audit write failed", exc_info=True)


# ---------------------------------------------------------------------------
# What the model says, made concrete
# ---------------------------------------------------------------------------

_SETTINGS_PAGES: tuple[tuple[str, str], ...] = (
    (r"windows\s+update|updates?", "ms-settings:windowsupdate"),
    (r"advanced\s+display", "ms-settings:display-advanced"),
    (r"graphics", "ms-settings:display-advancedgraphics"),
    (r"night\s+light", "ms-settings:nightlight"),
    (r"display|screen\s+resolution|resolution|brightness|scale", "ms-settings:display"),
    (r"sound|audio|speakers?|microphone\s+volume", "ms-settings:sound"),
    (r"bluetooth|devices?", "ms-settings:bluetooth"),
    (r"wi-?fi|wireless", "ms-settings:network-wifi"),
    (r"vpn", "ms-settings:network-vpn"),
    (r"hotspot", "ms-settings:network-mobilehotspot"),
    (r"airplane|flight\s+mode", "ms-settings:network-airplanemode"),
    (r"network|internet|ethernet", "ms-settings:network"),
    (r"default\s+apps?", "ms-settings:defaultapps"),
    (r"startup(\s+apps?)?", "ms-settings:startupapps"),
    (r"(installed\s+)?apps|programs|apps\s*&\s*features", "ms-settings:appsfeatures"),
    (r"battery", "ms-settings:batterysaver"),
    (r"power|sleep", "ms-settings:powersleep"),
    (r"storage|disk\s+space", "ms-settings:storagesense"),
    (r"notifications?", "ms-settings:notifications"),
    (r"focus|do\s+not\s+disturb", "ms-settings:quiethours"),
    (r"background|wallpaper", "ms-settings:personalization-background"),
    (r"themes?", "ms-settings:themes"),
    (r"colou?rs?|dark\s+mode|light\s+mode", "ms-settings:colors"),
    (r"lock\s+screen", "ms-settings:lockscreen"),
    (r"personali[sz]ation", "ms-settings:personalization"),
    (r"taskbar", "ms-settings:taskbar"),
    (r"touchpad", "ms-settings:devices-touchpad"),
    (r"mouse", "ms-settings:mousetouchpad"),
    (r"keyboard|typing", "ms-settings:typing"),
    (r"printers?|scanners?", "ms-settings:printers"),
    (r"date|time|clock|time\s+zone", "ms-settings:dateandtime"),
    (r"language|region", "ms-settings:regionlanguage"),
    (r"camera", "ms-settings:privacy-webcam"),
    (r"microphone", "ms-settings:privacy-microphone"),
    (r"privacy", "ms-settings:privacy"),
    (r"sign[\s-]?in\s+options|password|pin", "ms-settings:signinoptions"),
    (r"accounts?|your\s+info", "ms-settings:yourinfo"),
    (r"about|system\s+info|pc\s+name|specs", "ms-settings:about"),
    (r"game\s+mode|gaming", "ms-settings:gaming-gamemode"),
    (r"accessibility|ease\s+of\s+access", "ms-settings:easeofaccess"),
    (r"multitasking|snap", "ms-settings:multitasking"),
    (r"clipboard", "ms-settings:clipboard"),
    (r"recovery", "ms-settings:recovery"),
    (r"activation", "ms-settings:activation"),
    (r"troubleshoot", "ms-settings:troubleshoot"),
    (r"security|defender|virus", "windowsdefender:"),
)
_KNOWN_FOLDERS = ("Downloads", "Documents", "Desktop", "Pictures", "Music", "Videos")
_SHELL_FOLDERS = {
    "this pc": "shell:MyComputerFolder",
    "my computer": "shell:MyComputerFolder",
    "recycle bin": "shell:RecycleBinFolder",
    "control panel": "shell:ControlPanelFolder",
}
_BROWSER_EXES = frozenset({"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
                           "vivaldi.exe"})
_SITE_WINDOW_TITLES = {"swiggy instamart": "instamart"}
_SEARCH_PAGE_TITLE = re.compile(r"\b(?:duckduckgo|google search|bing|yahoo search)\b", re.I)
#: Windows' own tools and apps people name, and what starts them.
_SYSTEM_TOOLS: dict[str, str] = {
    "device manager": "devmgmt.msc",
    "disk management": "diskmgmt.msc",
    "services": "services.msc",
    "event viewer": "eventvwr.msc",
    "task scheduler": "taskschd.msc",
    "computer management": "compmgmt.msc",
    "task manager": "taskmgr.exe",
    "control panel": "control.exe",
    "command prompt": "cmd.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "terminal": "wt.exe",
    "windows terminal": "wt.exe",
    "snipping tool": "ms-screenclip:",
    "clock": "ms-clock:",
    "alarms": "ms-clock:",
    "alarm": "ms-clock:",
    "microsoft store": "ms-windows-store:",
    "store": "ms-windows-store:",
    "photos": "ms-photos:",
    "camera app": "microsoft.windows.camera:",
    "maps": "bingmaps:",
    "calendar": "outlookcal:",
    "mail": "outlookmail:",
    "run": "shell:::{2559a1f3-21d7-11d4-bdaf-00c04f60b9f0}",
    "run dialog": "shell:::{2559a1f3-21d7-11d4-bdaf-00c04f60b9f0}",
    "system information": "msinfo32.exe",
    "resource monitor": "resmon.exe",
    "sound control panel": "mmsys.cpl",
    "network connections": "ncpa.cpl",
    "programs and features": "appwiz.cpl",
    "system properties": "sysdm.cpl",
    "power options": "powercfg.cpl",
    "mouse properties": "main.cpl",
    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
}
_URL = re.compile(r"^(?:https?://\S+|(?:www\.)?[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}(?:/\S*)?)$", re.I)


def settings_uri(text: str) -> str | None:
    """The ``ms-settings:`` page a request names, or ``None`` when it is not Settings."""
    low = _norm(text)
    says_settings = bool(re.search(r"\bsettings?\b", low))
    if not says_settings and not re.fullmatch(r"windows\s+update", low):
        return None
    rest = re.sub(r"\b(open|windows|pc|system|computer|the|my|settings?|page|app)\b", " ", low)
    rest = " ".join(rest.split())
    if "windows update" in low:
        return "ms-settings:windowsupdate"
    if not rest:
        return "ms-settings:"
    for pattern, uri in _SETTINGS_PAGES:
        if re.search(rf"\b(?:{pattern})\b", rest):
            return uri
    return "ms-settings:"


def open_target(text: str) -> tuple[str, str]:
    """``(kind, value)`` for an ``open`` step: settings, url, path, shell, system or app."""
    raw = text.strip().strip("\"'")
    uri = settings_uri(raw)
    if uri:
        return "settings", uri
    if _URL.match(raw) and " " not in raw:
        return "url", raw if raw.lower().startswith("http") else "https://" + raw
    low = _norm(raw)
    if low in _SYSTEM_TOOLS:
        return "system", _SYSTEM_TOOLS[low]
    if low in _SHELL_FOLDERS:
        return "shell", _SHELL_FOLDERS[low]
    for name in _KNOWN_FOLDERS:
        if low in (name.casefold(), f"my {name.casefold()}", f"{name.casefold()} folder"):
            return "path", str(Path.home() / name)
    expanded = os.path.expandvars(os.path.expanduser(raw))
    if re.match(r"^[a-zA-Z]:[\\/]|^\\\\", expanded) or expanded.startswith(("~", "/")):
        return "path", expanded
    return "app", raw


_SPOKEN: dict[str, str] = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
    "6": "six", "7": "seven", "8": "eight", "9": "nine", "+": "plus", "-": "minus",
    "*": "multiply by", "x": "multiply by", "×": "multiply by", "times": "multiply by",
    "/": "divide by", "÷": "divide by", "=": "equals", "equal": "equals",
    ".": "decimal separator", "%": "percent", "c": "clear", "ce": "clear entry",
    "back": "backspace", "sqrt": "square root",
}


def _norm(text: str) -> str:
    return " ".join(str(text).casefold().split())


def best_control(controls: list[Control], target: str) -> Control | None:
    """The control a person means by ``target``, or ``None`` when nothing fits."""
    want = _norm(target).strip("'\"")
    if not want:
        return None
    wants = {want}
    if want in _SPOKEN:
        wants.add(_SPOKEN[want])
    words = want.split()
    ranked: list[tuple[float, int, Control]] = []
    for index, control in enumerate(controls):
        name = _norm(control.name)
        if not name:
            continue
        if name in wants:
            score = 100.0
        elif any(name.startswith(w) for w in wants):
            score = 80.0
        elif any(w in name for w in wants):
            score = 65.0
        elif words and all(w in name for w in words):
            score = 55.0
        else:
            ratio = difflib.SequenceMatcher(None, want, name).ratio()
            if ratio < 0.78:
                continue
            score = 40.0 * ratio
        if control.role in CLICKABLE_ROLES:
            score += 6
        if not control.enabled:
            score -= 30
        # Shorter names that still match are the more exact match.
        score -= min(len(name) - len(want), 60) / 20 if len(name) > len(want) else 0
        ranked.append((score, index, control))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], item[1]))
    top_score = ranked[0][0]
    if top_score <= 20:
        return None
    # Repeated labels (for example, one Add button per product card) are not
    # enough to identify which control the person meant. Never guess by order.
    tied = [item for item in ranked if abs(item[0] - top_score) < 1e-9]
    return tied[0][2] if len(tied) == 1 else None


def _coords(text: str) -> tuple[int, int] | None:
    match = re.fullmatch(
        r"\s*\(?\s*([+-]?\d{1,5})\s*[, ]\s*([+-]?\d{1,5})\s*\)?\s*",
        str(text),
    )
    return (int(match.group(1)), int(match.group(2))) if match else None


def _steps(args: dict[str, Any]) -> list[dict[str, Any]]:
    """The call's steps: ``steps`` as given, or the top-level actions in a fixed order."""
    raw = args.get("steps")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:  # Return invalid step descriptions as a user-visible tool error.
            raise ValueError("steps must be valid JSON.") from exc
    steps: list[dict[str, Any]] = []
    if raw is not None:
        if not isinstance(raw, list):
            raise ValueError("steps must be a list of action objects.")
        if not all(isinstance(s, dict) for s in raw):
            raise ValueError("Every item in steps must be an action object.")
        steps = [dict(s) for s in raw]
    else:
        single = {k: args[k] for k in ("app", "into", "enter", "submit", "clear", "button", "double", "see",
                                       "secret", "on", "to", "value") if k in args}
        fold_enter_key = (
            args.get("type") is not None
            and isinstance(args.get("keys"), str)
            and _norm(args["keys"]).replace(" ", "") in {"enter", "return"}
        )
        if fold_enter_key:
            single["enter"] = True
        for action in ("open", "navigate", "click", "set", "drag", "hover", "type", "keys",
                       "scroll", "window", "wait", "look"):
            if action == "keys" and fold_enter_key:
                continue
            if args.get(action) not in (None, "", False):
                action_value = args[action]
                step_options = single
                if (
                    action == "type"
                    and _norm(str(action_value)) in {"type", "text"}
                    and isinstance(args.get("value"), str)
                ):
                    action_value = args["value"]
                    step_options = {key: value for key, value in single.items() if key != "value"}
                steps.append({**step_options, action: action_value})
        if not steps and (args.get("app") or args.get("see")):
            steps.append({**single, "look": True})
    for step in steps:
        if (
            _norm(str(step.get("type", ""))) in {"type", "text"}
            and isinstance(step.get("value"), str)
        ):
            step["type"] = step.pop("value")
        if (
            "type" in step
            and isinstance(step.get("keys"), str)
            and _norm(step["keys"]).replace(" ", "") in {"enter", "return"}
        ):
            if step.get("enter") is False:
                raise ValueError("Use either enter=false or keys=enter, not both.")
            step.pop("keys")
            step["enter"] = True
        if "submit" in step:
            submit = step.pop("submit")
            if not isinstance(submit, bool):
                raise ValueError("submit must be true or false.")
            if "enter" in step and step["enter"] != submit:
                raise ValueError("Use either enter or submit, not conflicting values.")
            step["enter"] = submit
        if "navigate" in step:
            if "open" in step:
                raise ValueError("Use either open or navigate for a step, not both.")
            if not isinstance(step["navigate"], str) or not step["navigate"].strip():
                raise ValueError("navigate must contain a URL or page name as text.")
            # Models commonly use the natural action name "navigate" even
            # though the underlying operation is opening a URL in the browser.
            step["open"] = step.pop("navigate")
    allowed = {*_ACTIONS, "into", "enter", "clear", "button", "double", "see",
               "secret", "on", "to", "value"}
    for number, step in enumerate(steps, 1):
        unknown = set(step) - allowed
        actions = [key for key in _ACTIONS if key != "app" and key in step]
        if unknown or not actions:
            raise ValueError(
                f"Invalid step {number}: "
                + (f"unknown keys {', '.join(sorted(unknown))}. " if unknown else "no action. ")
                + "Use open, click, type, keys or scroll; type contains the text itself."
            )
        if len(actions) > 1:
            raise ValueError(
                f"Step {number} contains multiple UI actions ({', '.join(actions)}). "
                "Use exactly one UI action per call and inspect the returned state before continuing."
            )
    # A small model may ignore maxItems=1 and send a whole plan. Run only its
    # first action so the desktop is inspected between actions.
    return steps[:_MAX_STEPS]


def _requested_step_count(args: dict[str, Any]) -> int:
    raw = args.get("steps")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return 0  # _steps() reports the actionable parse error.
    return len(raw) if isinstance(raw, list) else 0


# ---------------------------------------------------------------------------
# The pilot
# ---------------------------------------------------------------------------


class Desk(Protocol):
    def windows(self) -> list[Window]: ...
    def foreground(self) -> int: ...
    def is_window(self, handle: int) -> bool: ...
    def activate(self, win: Window) -> bool: ...
    def window_op(self, win: Window, op: str) -> None: ...
    def controls(self, win: Window) -> list[Control]: ...
    def invoke(self, control: Control) -> bool: ...
    def focused_text(self) -> str | None: ...
    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None: ...
    def scroll(self, x: int, y: int, notches: int) -> None: ...
    def hover(self, x: int, y: int) -> None: ...
    def drag(self, start: tuple[int, int], end: tuple[int, int]) -> None: ...
    def set_value(self, control: Control, value: str) -> str | None: ...
    def press(self, combo: str) -> None: ...
    def type_text(self, text: str) -> None: ...


class StepFailed(Exception):
    """A step could not be done; the message goes to the model."""


@dataclass
class _Run:
    window: Window | None = None
    controls: list[Control] = field(default_factory=list)
    done: list[str] = field(default_factory=list)
    want_picture: bool = False
    #: A secret was typed this call: the readback leaves field contents out.
    secret: bool = False
    #: The window's controls before the last click or key, so the readback
    #: can lead with what that action brought up (a menu, a popup, a page).
    baseline: set[tuple[str, str]] | None = None
    #: The Start menu, search, Run box or another shell surface is open: the
    #: next typing and keys go to it, not back to the app window.
    shell: bool = False


def web_lookup_url(name: str) -> str:
    """Resolve well-known services directly; search is only a generic fallback."""
    compact = re.sub(r"[^a-z0-9]+", "", name.casefold())
    if compact in {"instamart", "swiggyinstamart", "swiginstamart", "swiggyinstomach"} or (
        "swig" in compact and ("insta" in compact or "stomach" in compact)
    ):
        return "https://www.swiggy.com/instamart"
    return "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(f"!ducky {name}")


def _launch(kind: str, value: str) -> str:
    """Open a settings page, URL, folder or shell place (never an app; see open_app).

    A web page opens in the browser the owner already has running (live
    2026-10-27 the https default was Edge while he worked in Chrome, so the
    page went to a cold Edge and the next steps to another window). Returns
    that browser's exe name, or ``""``.
    """
    if kind == "shell" or (kind == "system" and value.startswith("shell:")):
        subprocess.Popen(  # noqa: S603, S607 - fixed program
            ["explorer.exe", value], shell=False,
            creationflags=NO_WINDOW_CREATIONFLAGS, close_fds=True,
        )
        return ""
    if kind == "url":
        from jarvis.platform.open_path import preferred_browser_exe  # noqa: PLC0415

        exe = preferred_browser_exe()
        if exe:
            subprocess.Popen(  # noqa: S603 - a browser and an https URL
                [exe, value], shell=False,
                creationflags=NO_WINDOW_CREATIONFLAGS, close_fds=True,
            )
            return Path(exe).name
    if not hasattr(os, "startfile"):
        raise StepFailed("Opening that needs Windows.")
    if kind == "path" and not Path(value).exists():
        raise StepFailed(f"{value} does not exist on this computer.")
    try:
        os.startfile(value)  # type: ignore[attr-defined]  # noqa: S606 - the owner's own request
    except FileNotFoundError:
        raise StepFailed(f"{value} is not installed on this PC.") from None
    return ""


class PcPilot:
    """The ``pc_pilot`` tool; ``desk`` and ``open_app`` are swappable for tests."""

    name: str = PILOT
    risk_tier: RiskTier = "monitor"
    description: str = (
        "Drive any app or window on this PC like a person: open apps, Settings pages, "
        "folders and websites; click buttons by the name you see; type; press keys; "
        "scroll; minimize, maximize, focus or close windows. Make one UI action per call. "
        "If a call contains multiple steps, only the first is run; inspect the returned "
        "controls and screen before choosing the next action. "
        "Use 'steps' with one action, for example [{\"open\":\"calculator\"}], then "
        "[{\"click\":\"7\"}]. For an app-and-action, open it first, inspect its window, "
        "then continue in a fresh call. "
        "Websites work the same way in the owner's own Chrome: shopping sites such as "
        "Swiggy Instamart, Amazon or Blinkit; search, inspect the result, and click a "
        "uniquely identified product control in separate calls. If repeated controls "
        "share a name, specify a unique target or use see=true with visible x,y coordinates. "
        "Adding to a cart is fine, paying is asked first. A name not on screen is scrolled to. "
        "Step keys: open or navigate (app, page, folder or URL; navigate reuses the current browser "
        "window), app (which window to act in), "
        "click (a control's name, or 'x,y'), type (text; add into=field name, "
        "enter=true or submit=true to press Enter, secret=true for a password), "
        "keys ('ctrl+s', 'enter', 'win+d'), "
        "scroll ('down'/'up'/'down 10'; on=a control to scroll inside), drag (a control "
        "or 'x,y', with to=where to drop), set (a slider or field by name, with "
        "value=, e.g. set=Brightness value=60; use it for every slider), "
        "hover (point at a control), "
        "window (focus/minimize/maximize/restore/close), wait (seconds), look (read "
        "the window). It answers with the window's controls after the steps; set "
        "see=true to also get a picture of the screen."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "maxItems": _MAX_STEPS,
                "description": (
                    "Use one UI action per call. If multiple steps are sent, only the first "
                    "runs; inspect its returned state before the next call."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "open": {"type": "string"},
                        "navigate": {
                            "type": "string",
                            "description": "Open a URL in the current browser window; alias for open",
                        },
                        "app": {"type": "string"},
                        "click": {"type": "string"},
                        "type": {"type": "string"},
                        "set": {"type": "string"},
                        "drag": {"type": "string"},
                        "to": {"type": "string"},
                        "hover": {"type": "string"},
                        "into": {"type": "string"},
                        "secret": {"type": "boolean"},
                        "on": {"type": "string"},
                        "enter": {"type": "boolean"},
                        "submit": {
                            "type": "boolean",
                            "description": "Alias for enter when used with type",
                        },
                        "clear": {"type": "boolean"},
                        "keys": {"type": "string"},
                        "scroll": {"type": "string"},
                        "window": {"type": "string", "enum": list(_WINDOW_OPS)},
                        "wait": {"type": "number"},
                        "look": {"type": "boolean"},
                        "see": {"type": "boolean"},
                        "button": {"type": "string", "enum": ["left", "right", "middle"]},
                        "double": {"type": "boolean"},
                        "value": {"type": ["string", "number"]},
                    },
                },
            },
            "open": {"type": "string", "description": "App, Settings page, folder or URL"},
            "navigate": {
                "type": "string",
                "description": "Open a URL in the current browser window; alias for open",
            },
            "app": {"type": "string", "description": "The window to act in, e.g. 'Calculator'"},
            "click": {"type": "string"},
            "type": {"type": "string"},
            "set": {"type": "string"},
            "drag": {"type": "string"},
            "to": {"type": "string"},
            "hover": {"type": "string"},
            "into": {"type": "string"},
            "secret": {"type": "boolean"},
            "clear": {"type": "boolean"},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
            "double": {"type": "boolean"},
            "enter": {"type": "boolean"},
            "submit": {
                "type": "boolean",
                "description": "Alias for enter when used with type",
            },
            "keys": {"type": "string"},
            "on": {"type": "string"},
            "scroll": {"type": "string"},
            "window": {"type": "string", "enum": list(_WINDOW_OPS)},
            "wait": {"type": "number"},
            "look": {"type": "boolean"},
            "see": {"type": "boolean", "description": "Also return a picture of the screen"},
            "value": {"type": ["string", "number"]},
        },
    }

    def __init__(
        self,
        desk: Desk | None = None,
        *,
        open_app: Any = None,
        see_screen: Any = None,
        policy: PilotPolicy | None = None,
        audit_path: Path | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self._desk = desk
        self._open_app = open_app
        self._see_screen = see_screen
        self._policy = policy
        self._audit_path = audit_path
        self._sleep = sleep
        self._last: Window | None = None
        self._last_at = 0.0
        self._target_unresolved = False

    @property
    def has_recent_target(self) -> bool:
        """Keep short follow-ups on the external app rather than Jarvis's UI."""
        return self._last is not None and time.monotonic() - self._last_at < 300

    # -- risk ---------------------------------------------------------------
    def risk_tier_for_args(self, args: dict[str, Any]) -> RiskTier:
        policy = self._policy_now()
        try:
            steps = _steps(args or {})
        except ValueError:
            return "monitor"  # execute() reports malformed input without taking action
        return "ask" if any(policy.needs_yes(s) for s in steps) else "monitor"

    def requires_explicit_confirmation_for_args(self, args: dict[str, Any]) -> bool:
        """The pilot's destructive-action policy cannot be disabled by app defaults."""
        policy = self._policy_now()
        try:
            steps = _steps(args or {})
        except ValueError:
            return False  # execute() returns the validation error without taking action
        return any(policy.needs_yes(step) for step in steps)

    # -- run ------------------------------------------------------------------
    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        requested_step_count = _requested_step_count(args or {})
        try:
            steps = _steps(args or {})
        except ValueError as exc:  # Return invalid step descriptions as a user-visible tool error.
            return ToolResult(False, None, f"Invalid steps: {exc}")
        if not steps:
            return ToolResult(False, None, "Give at least one step: open, click, type, keys ...")
        allowed = {*_ACTIONS, "into", "enter", "clear", "button", "double", "see",
                   "secret", "on", "to", "value"}
        for number, step in enumerate(steps, 1):
            unknown = set(step) - allowed
            actions = [
                action for action in _ACTIONS
                if action in step and action not in ("app",)
            ]
            if unknown or not actions:
                return ToolResult(
                    False, None,
                    f"Invalid step {number}: "
                    + (f"unknown keys {', '.join(sorted(unknown))}. " if unknown else "no action. ")
                    + "Use open, click, type, keys or scroll; type contains the text itself.",
                )
            if len(actions) > 1:
                return ToolResult(
                    False, None,
                    f"Invalid step {number}: use exactly one UI action per call "
                    "and inspect its returned state before the next action.",
                )
        desk = self._desk_now()
        if desk is None:
            return ToolResult(False, None, "The PC pilot needs Windows.")
        policy = self._policy_now()
        # Desktop actions must always be grounded in a fresh screen image.
        # UIA readback remains useful, but it misses custom-drawn controls and
        # can disagree with what is actually visible (e.g. Settings menus).
        run = _Run(want_picture=True)
        error = ""
        trace_id = getattr(ctx, "trace_id", None)
        for number, step in enumerate(steps, 1):
            step_started = time.monotonic()
            failure_type = ""
            failure_reason = ""
            try:
                run.want_picture = run.want_picture or bool(step.get("see"))
                await self._step(desk, policy, run, step, ctx)
            except StepFailed as exc:  # The run result below reports the failed step and reason.
                failure_type = type(exc).__name__
                failure_reason = (
                    "[redacted secret action]" if step.get("secret") else str(exc)[:300]
                )
                error = f"Step {number} ({_describe(step)}) failed: {exc}"
                run.want_picture = True
                break
            except Exception as exc:  # noqa: BLE001 - a Win32 or UIA failure goes back to the model
                log.info("pc_pilot: step %d failed", number, exc_info=True)
                failure_type = type(exc).__name__
                failure_reason = (
                    "[redacted secret action]" if step.get("secret") else str(exc)[:300]
                )
                error = f"Step {number} ({_describe(step)}) failed: {exc}"
                break
            finally:
                audit_entry = {
                    "ts": _dt.datetime.now().isoformat(timespec="seconds"),
                    "step_number": number,
                    "step": _audit_view(step),
                    "window": run.window.title if run.window else "",
                    "ok": not failure_type,
                    "elapsed_ms": max(0, int((time.monotonic() - step_started) * 1000)),
                }
                if trace_id is not None:
                    audit_entry["trace_id"] = str(trace_id)
                if failure_type:
                    audit_entry["failure_type"] = failure_type
                    audit_entry["failure_reason"] = failure_reason
                _audit(self._audit_path_now(), audit_entry)
        report = await asyncio.to_thread(self._readback, desk, run)
        text = "\n".join(filter(None, [
            "Actions sent: " + "; ".join(run.done) + "." if run.done else "",
            (
                f"Only the first of {requested_step_count} requested actions was sent; "
                "the remaining actions were not run. Inspect this screen before choosing the next action."
                if requested_step_count > 1 else ""
            ),
            error,
            report,
        ]))
        output: dict[str, Any] | str = text
        few = len([c for c in run.controls if c.name]) < _FEW_CONTROLS
        if run.want_picture or few:
            picture = await self._picture(ctx)
            if picture is not None:
                output = {"description": text, "_image": picture}
        if error:
            return ToolResult(False, output, error)
        if run.window is not None:
            self._last, self._last_at = run.window, time.monotonic()
        return ToolResult(True, output)

    async def _step(
        self, desk: Desk, policy: PilotPolicy, run: _Run, step: dict[str, Any], ctx: Any
    ) -> None:
        if step.get("see"):
            run.want_picture = True
        if step.get("secret"):
            run.secret = True
        if "open" in step:
            await self._do_open(desk, run, str(step["open"]), ctx)
            if not any(k in step for k in _ACTIONS if k not in ("open", "app", "wait", "look")):
                return
        if "wait" in step:
            seconds = max(0.0, min(float(step.get("wait") or 0), 10.0))
            await asyncio.to_thread(self._sleep, seconds)
            run.done.append(f"waited {seconds:g} s")
            return
        run.window = await asyncio.to_thread(self._target, desk, run, step.get("app"))
        if run.window is None:
            raise StepFailed(
                "No app window is open to act in. Open the app first."
                if not step.get("app") else f"No open window matches {step['app']!r}."
            )
        reason = policy.refusal(step, run.window, field_is_password=False)
        if reason:
            raise StepFailed(reason)
        await asyncio.to_thread(self._act, desk, policy, run, step)

    async def _do_open(self, desk: Desk, run: _Run, what: str, ctx: Any) -> None:
        kind, value = open_target(what)
        before_windows = await asyncio.to_thread(desk.windows)
        before = {w.handle for w in before_windows}
        previous_targets = (run.window, self._last)
        self._target_unresolved = True
        self._last, self._last_at = None, 0.0
        web_for = ""
        if kind == "app":
            opener = self._open_app_now()
            result = await opener.execute({"app_name": value}, ctx)
            if not getattr(result, "success", False):
                error = str(getattr(result, "error", "") or f"Could not open {value}.")
                if not _NOT_INSTALLED.search(error):
                    raise StepFailed(error)
                # Live 2026-10-27 "open swiggy instamart and add ... to my cart":
                # no such app, but the service has a website. Its name goes to
                # the browser, which lands on the site's own page.
                web_for, kind, value = value, "url", web_lookup_url(value)
        # Resolve this after the app-name-to-URL fallback above: services such
        # as Instamart first look like app names, then become browser URLs.
        existing_browser = None
        if kind == "url":
            for target in previous_targets:
                if target is None or target.exe.casefold() not in _BROWSER_EXES:
                    continue
                if not desk.is_window(target.handle):
                    continue
                existing_browser = next(
                    (w for w in before_windows if w.handle == target.handle), None
                )
                if existing_browser is not None:
                    break
        browser = ""
        if kind == "url" and existing_browser is not None:
            # Stay in the window the user asked for (especially when Chrome is
            # open but is not Windows' default browser). Ctrl+L avoids querying
            # Chromium's sometimes unstable accessibility tree just to find the
            # address bar.
            run.window = existing_browser
            await asyncio.to_thread(self._front, desk, existing_browser)
            await asyncio.to_thread(desk.press, "ctrl+l")
            await asyncio.to_thread(desk.type_text, value)
            await asyncio.to_thread(desk.press, "enter")
            browser = existing_browser.exe
        elif kind != "app":
            browser = await asyncio.to_thread(_launch, kind, value)
        # Windows' own tools are titled by the name people use for them.
        hint = {"settings": "settings", "url": "", "shell": "", "system": _norm(what),
                "path": Path(value).name}.get(kind, value)
        run.window = await asyncio.to_thread(
            self._wait_for_window, desk, before, hint, kind == "url",
            kind in ("system", "path", "url"), browser,
        )
        if run.window is None and kind == "url":
            raise StepFailed(
                f"{value} was sent to the browser but no browser window came up. "
                "Call again in a moment."
            )
        if run.window is None:
            # A slow program (Device Manager took over 15 s on a busy PC):
            # say so, and never let the next step land in another window.
            raise StepFailed(
                f"{what} was started but its window has not appeared yet. "
                f"Call again with app={what!r} in a moment."
            )
        if run.window is not None:
            self._target_unresolved = False
            self._last = run.window
            await asyncio.to_thread(self._front, desk, run.window)
            if kind == "url":
                run.window = await asyncio.to_thread(self._page_loaded, desk, run.window, value)
            await asyncio.to_thread(self._settle, desk, run.window)
            if (
                kind == "app"
                and run.window.handle in before
                and run.window.exe.casefold() == "notepad.exe"
            ):
                # "Open Notepad and type" means a fresh page. An already open
                # Notepad shows one of the owner's own notes, which the typing
                # would land in (live 2026-10-26: ten open tabs).
                await asyncio.to_thread(desk.press, "ctrl+n")
                await asyncio.to_thread(self._sleep, 0.7)
                # Notepad opens the page as a tab or, by its own setting, as
                # a new window; the window in front now holds it.
                front = await asyncio.to_thread(desk.foreground)
                wins = await asyncio.to_thread(desk.windows)
                run.window = next((w for w in wins if w.handle == front), run.window)
                self._last = run.window
        run.done.append(
            f"opened {what}" if not web_for
            else f"found no app named {web_for!r}, so opened its website in the browser"
        )

    # -- blocking helpers (run in a thread) ---------------------------------
    def _wait_for_window(
        self, desk: Desk, before: set[int], hint: str, web: bool = False, strict: bool = False,
        browser: str = "",
    ) -> Window | None:
        deadline = time.monotonic() + _OPEN_WAIT_S
        if web:
            # A page usually opens as a tab of the browser already running,
            # so no new window appears: the browser in front is the target.
            self._sleep(1.0)
        while True:
            wins = desk.windows()
            if web:
                # Prefer the browser window that received the navigation. A
                # process-name match alone is ambiguous when several profiles
                # or Chrome windows are open; never pick an arbitrary one.
                exes = {browser.casefold()} if browser else _BROWSER_EXES
                matches = [w for w in wins if w.exe.casefold() in exes]
                foreground = desk.foreground()
                front = next((w for w in matches if w.handle == foreground), None)
                if front is not None:
                    return front
                fresh = [w for w in matches if w.handle not in before]
                if len(fresh) == 1:
                    return fresh[0]
                if not fresh and len(matches) == 1:
                    return matches[0]
                if time.monotonic() > deadline:
                    # A browser process match is not enough to prove which
                    # window received the navigation.
                    return None
                self._sleep(0.25)
                continue
            if hint:
                named = _match_window(wins, hint)
                if named is not None:
                    return named
            fresh = [w for w in wins if w.handle not in before]
            if fresh and not hint:
                return fresh[0]
            if time.monotonic() > deadline:
                # An arbitrary foreground/new window is never evidence that
                # the requested app opened. Do not send later input into it.
                return None
            self._sleep(0.25)

    def _page_loaded(self, desk: Desk, win: Window, url: str) -> Window:
        """The browser window once its title is the page's, not the address typed."""
        typed = _norm(re.sub(r"^https?://(www\.)?", "", url).rstrip("/"))
        instamart = "swiggy.com/instamart" in typed
        deadline = time.monotonic() + _PAGE_WAIT_S
        while time.monotonic() < deadline:
            fresh = next((w for w in desk.windows() if w.handle == win.handle), win)
            title = _norm(fresh.title)
            if _SEARCH_PAGE_TITLE.search(title) and (
                "duckduckgo.com" in typed or instamart
            ):
                raise StepFailed(
                    f"The browser stayed on search results ('{fresh.title}') instead of opening "
                    f"{url}; follow-up actions were stopped."
                )
            if instamart and not re.search(r"\b(?:swiggy|instamart)\b", title):
                self._sleep(0.4)
                continue
            # A !ducky lookup may show its own page while redirecting to a site.
            hop = "duckduckgo" in typed and "duckduckgo" in title
            if title and not title.startswith(typed) and "loading" not in title and not hop:
                return fresh
            self._sleep(0.4)
        if instamart:
            raise StepFailed(
                "Could not confirm that Swiggy Instamart finished opening. "
                "Follow-up actions were stopped."
            )
        return win

    def _settle(self, desk: Desk, win: Window) -> None:
        """Wait until a fresh window stops adding controls (a page still drawing)."""
        deadline = time.monotonic() + _OPEN_SETTLE_S
        last = -1
        while time.monotonic() < deadline:
            try:
                count = len(desk.controls(win))
            except Exception:  # noqa: BLE001 - a window still building its tree: try again
                count = -1
            if count > 0 and count == last:
                return
            last = count
            self._sleep(0.4)

    def _target(self, desk: Desk, run: _Run, app: Any) -> Window | None:
        wins = desk.windows()
        if app:
            preferred = run.window.handle if run.window is not None else (
                self._last.handle if self._last is not None else None
            )
            found = _match_window(wins, str(app), preferred_handle=preferred)
            if found is not None:
                self._last = found
                self._target_unresolved = False
            return found
        if self._target_unresolved:
            return None
        for candidate in (run.window, self._last):
            if candidate is not None and desk.is_window(candidate.handle):
                fresh = next((w for w in wins if w.handle == candidate.handle), None)
                if fresh is not None:
                    return fresh
        # The window right behind Jarvis's own: what the owner was looking at.
        return wins[0] if wins else None

    def _front(self, desk: Desk, win: Window) -> None:
        """Bring ``win`` forward; input must never land in another window."""
        if desk.foreground() == win.handle:
            return
        desk.activate(win)
        if desk.foreground() != win.handle:
            self._sleep(0.3)
            desk.activate(win)
        if desk.foreground() != win.handle:
            raise StepFailed(f"Windows would not bring '{win.title}' to the front.")

    def _look(self, desk: Desk, run: _Run) -> list[Control]:
        assert run.window is not None
        try:
            run.controls = desk.controls(run.window)
        except UIAQueryError as exc:
            raise StepFailed(
                "Windows could not read this window's accessibility controls after retry. "
                "The screen picture is attached; use see=true with a visible x,y target, or retry."
            ) from exc
        return run.controls

    def _find(self, desk: Desk, run: _Run, target: str) -> Control:
        control = best_control(self._look(desk, run), target)
        if control is None:
            # A page or pane that is still loading: look once more.
            self._sleep(0.9)
            control = best_control(self._look(desk, run), target)
        scrolls = 0
        while control is None and scrolls < _FIND_SCROLLS and _coords(target) is None:
            # Settings and long web pages only build the rows in view: live
            # 2026-10-27 "More sound settings" sat below the fold of Sound.
            # Scroll down and look again until the page stops moving.
            seen = {(c.role, c.name, c.bounds) for c in run.controls}
            desk.scroll(*win_center(run.controls), -5)
            scrolls += 1
            self._sleep(0.6)
            control = best_control(self._look(desk, run), target)
            if control is None and {(c.role, c.name, c.bounds) for c in run.controls} == seen:
                break
        if control is not None and scrolls:
            run.done.append(f"scrolled down to {control.name}")
        if control is None:
            raise StepFailed(
                f"No unique control named like {target!r} in '{run.window.title}'. "  # type: ignore[union-attr]
                "If multiple controls share that label, specify more context or use "
                "see=true and a visible x,y coordinate."
            )
        return control

    def _point(self, desk: Desk, run: _Run, target: str) -> tuple[int, int]:
        return _coords(target) or self._find(desk, run, target).center()

    def _act(self, desk: Desk, policy: PilotPolicy, run: _Run, step: dict[str, Any]) -> None:
        win = run.window
        assert win is not None
        if "window" in step:
            op = str(step["window"]).casefold()
            if op not in _WINDOW_OPS:
                raise StepFailed(f"window must be one of {', '.join(_WINDOW_OPS)}.")
            if op == "focus":
                self._front(desk, win)
            else:
                desk.window_op(win, op)
            run.done.append(f"{op} {win.title}")
            self._sleep(_SETTLE_S)
            return
        shell_keys = "keys" in step and _norm(str(step["keys"])).replace(" ", "") in _SHELL_KEYS
        if run.shell and not any(k in step for k in ("click", "scroll", "app")):
            # Typing into the Start menu or Run box: bringing the app back
            # to the front would close it.
            self._act_on_shell(desk, run, step)
            return
        run.shell = False
        if shell_keys:
            desk.press(str(step["keys"]))
            run.shell = True
            run.done.append(f"pressed {step['keys']}")
            self._sleep(0.8)
            return
        self._front(desk, win)
        if any(k in step for k in ("click", "keys", "drag", "hover")) or step.get("enter"):
            run.baseline = {(c.role, c.name) for c in self._look(desk, run)}
        if "hover" in step:
            desk.hover(*self._point(desk, run, str(step["hover"])))
            run.done.append(f"pointed at {step['hover']}")
            self._sleep(0.8)
        if "set" in step:
            control = self._find(desk, run, str(step["set"]))
            if step.get("value") in (None, ""):
                raise StepFailed("set needs value=: the number or text to set it to.")
            try:
                now = desk.set_value(control, str(step["value"]))
            except ValueError as exc:
                raise StepFailed(str(exc)) from None
            if now is None:
                raise StepFailed(f"{control.name!r} cannot be set directly; click or type instead.")
            run.done.append(f"set {control.name} to {now}")
            self._sleep(_SETTLE_S)
        if "drag" in step:
            start = self._point(desk, run, str(step["drag"]))
            if not step.get("to"):
                raise StepFailed("A drag needs to=: where to drop it (a control's name or 'x,y').")
            end = self._point(desk, run, str(step["to"]))
            desk.drag(start, end)
            run.done.append(f"dragged {step['drag']} to {step['to']}")
            self._sleep(_SETTLE_S)
        if "click" in step:
            target = str(step["click"])
            button = str(step.get("button") or "left")
            count = 2 if step.get("double") else 1
            point = _coords(target)
            if point is not None:
                desk.click(*point, button=button, count=count)
                run.done.append(f"clicked at {point[0]},{point[1]}")
            else:
                control = self._find(desk, run, target)
                pressed = button == "left" and count == 1 and control.role in (
                    "Button", "SplitButton", "MenuItem", "CheckBox", "RadioButton"
                ) and desk.invoke(control)
                if not pressed:
                    desk.click(*control.center(), button=button, count=count)
                run.done.append(f"clicked {control.name!r}")
            self._sleep(_SETTLE_S)
        if "type" in step:
            text = str(step["type"])
            if step.get("into"):
                field_control = self._find(desk, run, str(step["into"]))
                if field_control.password and not step.get("secret"):
                    raise StepFailed(_SECRET_HINT)
                desk.click(*field_control.center())
                self._sleep(0.15)
            if not step.get("secret") and any(
                c.focused and c.password for c in self._look(desk, run)
            ):
                raise StepFailed(_SECRET_HINT)
            if step.get("clear"):
                desk.press("ctrl+a")
                desk.press("backspace")
            before = desk.focused_text()
            desk.type_text(text)
            if step.get("enter"):
                desk.press("enter")
            self._sleep(0.2)
            after = desk.focused_text()
            if (
                after is not None
                and not step.get("enter")
                and _norm(text) not in _norm(after)
                and not (before or "").strip()
            ):
                # Typing went missing or jumbled in an empty field: set it once more.
                desk.press("ctrl+a")
                desk.type_text(text)
                self._sleep(0.2)
                after = desk.focused_text()
            shown = (
                f" (the field now holds {after[:120]!r})"
                if after is not None and not step.get("secret")
                else ""
            )
            run.done.append(f"typed {len(text)} characters{shown}")
        if "keys" in step:
            combo = str(step["keys"])
            try:
                key_codes(combo)
            except ValueError as exc:
                raise StepFailed(str(exc)) from exc
            desk.press(combo)
            run.done.append(f"pressed {combo}")
            self._sleep(_SETTLE_S)
        if "scroll" in step:
            way = _norm(str(step["scroll"]))
            notches = 5 if way in ("up", "page up") else -5
            match = re.search(r"\d+", way)
            if match:
                notches = int(match.group()) * (1 if "up" in way else -1)
            if step.get("on"):
                left, top = self._find(desk, run, str(step["on"])).center()
            else:
                left, top = win_center(self._look(desk, run))
            before = {
                (c.role, c.name, c.bounds)
                for c in self._look(desk, run)
            }
            desk.scroll(left, top, notches)
            self._sleep(0.6)
            after = {
                (c.role, c.name, c.bounds)
                for c in self._look(desk, run)
            }
            if after == before:
                raise StepFailed("Scroll input was sent, but visible movement could not be verified.")
            run.done.append(f"scrolled {'up' if notches > 0 else 'down'}")
        if "look" in step:
            run.done.append(f"read '{win.title}'")

    def _act_on_shell(self, desk: Desk, run: _Run, step: dict[str, Any]) -> None:
        if "type" in step:
            desk.type_text(str(step["type"]))
            run.done.append(f"typed {len(str(step['type']))} characters")
            if step.get("enter"):
                desk.press("enter")
                run.shell = False
        if "keys" in step:
            combo = str(step["keys"])
            try:
                key_codes(combo)
            except ValueError as exc:
                raise StepFailed(str(exc)) from exc
            desk.press(combo)
            run.done.append(f"pressed {combo}")
            if _norm(combo) in ("enter", "escape", "esc"):
                run.shell = False
        self._sleep(_SETTLE_S)

    def _readback(self, desk: Desk, run: _Run) -> str:
        if run.window is None or not desk.is_window(run.window.handle):
            return "No verified target window is available; no other window was selected."
        try:
            controls = self._look(desk, run)
        except Exception:  # noqa: BLE001 - an unreadable window still gets its picture
            log.debug("pc_pilot: readback failed", exc_info=True)
            run.controls = []
            return f"Window: '{run.window.title}' (its controls cannot be read)."
        return summarize(
            run.window, controls, None if run.secret else desk.focused_text(), run.baseline
        )

    # -- lazy parts -----------------------------------------------------------
    def _desk_now(self) -> Desk | None:
        if self._desk is None and sys.platform == "win32":
            from jarvis.platform.win_pilot import WinDesk  # noqa: PLC0415

            self._desk = WinDesk()
        return self._desk

    def _open_app_now(self) -> Any:
        if self._open_app is None:
            from jarvis.plugins.tool.open_app import OpenAppTool  # noqa: PLC0415

            self._open_app = OpenAppTool()
        return self._open_app

    def _policy_now(self) -> PilotPolicy:
        if self._policy is None:
            self._policy = PilotPolicy.load(_data_dir() / "pilot_policy.json")
        return self._policy

    def _audit_path_now(self) -> Path | None:
        if self._audit_path is None and self._desk is not None and sys.platform == "win32":
            self._audit_path = _data_dir() / "pilot_audit.jsonl"
        return self._audit_path

    async def _picture(self, ctx: Any) -> dict[str, str] | None:
        see = self._see_screen or _see_screen
        try:
            return await see(ctx)
        except Exception:  # noqa: BLE001 - no picture still leaves the text answer
            log.debug("pc_pilot: screen picture failed", exc_info=True)
            return None


def win_center(found: list[Control]) -> tuple[int, int]:
    """Where a scroll goes: the page or document, else the middle of the controls."""
    documents = [c for c in found if c.role == "Document"]
    if documents:
        return documents[0].center()
    if found:
        lefts = [c.bounds[0] for c in found]
        tops = [c.bounds[1] for c in found]
        rights = [c.bounds[0] + c.bounds[2] for c in found]
        bottoms = [c.bounds[1] + c.bounds[3] for c in found]
        return (min(lefts) + max(rights)) // 2, (min(tops) + max(bottoms)) // 2
    return 960, 540


def _match_window(
    wins: list[Window], name: str, *, preferred_handle: int | None = None,
) -> Window | None:
    original_want = _norm(name)
    site_title = _SITE_WINDOW_TITLES.get(original_want)
    if site_title:
        matches = [
            win for win in wins
            if win.exe.casefold() in _BROWSER_EXES and site_title in _norm(win.title)
        ]
        preferred = next((w for w in matches if w.handle == preferred_handle), None)
        if preferred is not None:
            return preferred
        return matches[0] if len(matches) == 1 else None
    want = original_want
    want = {"calc": "calculator", "file explorer": "explorer", "explorer": "explorer",
            "settings": "settings", "windows settings": "settings"}.get(want, want)
    stem = want.removesuffix(".exe")
    for win in wins:
        title = _norm(win.title)
        exe = win.exe.casefold().removesuffix(".exe")
        if title == want or exe == stem:
            return win
    for win in wins:
        if want in _norm(win.title) or (stem and stem in win.exe.casefold()):
            return win
    words = [w for w in want.split() if len(w) > 2]
    for win in wins:
        title = _norm(win.title)
        if words and all(w in title for w in words):
            return win
    return None


def summarize(
    win: Window,
    controls: list[Control],
    focused: str | None,
    baseline: set[tuple[str, str]] | None = None,
) -> str:
    """The window as the model reads it: title, what just appeared, then controls by name.

    Live 2026-10-26 YouTube's account switcher opened below 45 page links,
    so its accounts fell into "(and N more)". Controls that were not there
    before the last click or key come first.
    """
    seen: set[tuple[str, str]] = set()
    fresh: list[str] = []
    clickable: list[str] = []
    readable: list[str] = []
    for control in controls:
        key = (control.role, control.name)
        if key in seen or not control.name:
            continue
        seen.add(key)
        name = control.name if len(control.name) <= 80 else control.name[:77] + "..."
        line = f"- {control.role} '{name}'" + ("" if control.enabled else " (disabled)")
        if baseline is not None and key not in baseline and len(fresh) < _MAX_FRESH:
            fresh.append(line)
            continue
        (clickable if control.role in CLICKABLE_ROLES else readable).append(line)
    lines = [f"Window: '{win.title}'" + (f" ({win.exe})" if win.exe else "")]
    if fresh:
        lines.append("New on screen after the last step:")
        lines.extend(fresh)
    listed = clickable[:_MAX_LISTED] + readable[: max(0, _MAX_LISTED - len(clickable))][:15]
    if listed:
        lines.append("Controls (click them by name):")
        lines.extend(listed)
        hidden = len(clickable) + len(readable) - len(listed)
        if hidden > 0:
            lines.append(f"(and {hidden} more)")
    else:
        lines.append("No named controls; use see=true to look, or click at x,y.")
    if focused:
        lines.append(f"Focused field holds: {focused[:200]!r}")
    return "\n".join(lines)


def _describe(step: dict[str, Any]) -> str:
    for action in _ACTIONS:
        if action in step:
            value = step[action]
            if action == "type":
                return f"type {len(str(value))} characters"
            return f"{action} {value}" if value is not True else action
    return "step"


def _audit_view(step: dict[str, Any]) -> dict[str, Any]:
    view = {k: v for k, v in step.items() if k != "type"}
    if "type" in step:
        view["type_chars"] = len(str(step["type"]))
    return view


def _data_dir() -> Path:
    from jarvis.core.paths import user_data_dir  # noqa: PLC0415

    return user_data_dir() / "data"


async def _see_screen(ctx: Any) -> dict[str, str] | None:
    """One privacy-filtered screen frame, the same capture screen_snapshot uses."""
    import base64  # noqa: PLC0415

    from jarvis.appshot.service import record_turn_capture  # noqa: PLC0415
    from jarvis.plugins.tool.appshot import _app_bus  # noqa: PLC0415
    from jarvis.screen_context.turn import get_service  # noqa: PLC0415

    bus = _app_bus()
    service = get_service(bus=bus)
    outcome = await service.capture(trace_id=getattr(ctx, "trace_id", None))
    image = service.consume(outcome.handle_id) if outcome.handle_id else None
    if image is None:
        return None
    await record_turn_capture(image, bus=bus, trigger="tool")
    return {"mime": image.mime, "data": base64.b64encode(image.image).decode("ascii")}


__all__ = ["PILOT", "PcPilot", "PilotPolicy", "best_control", "open_target", "settings_uri",
           "summarize"]
