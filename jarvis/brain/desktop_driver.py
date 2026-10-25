"""Drive a desktop app in the background through CUA Driver, in one call.

Owner's test 2026-10-25: CUA Driver (trycua/cua) pressed Calculator's buttons
and typed into Notepad behind his own window, 0 focus changes in 6 runs. Added
to Jarvis as an MCP server, Gemma 4 E4B could not use it: it copied the window
id 5678 and pid 1234 out of a tool description, then typed into the wrong
control, because one button press there takes list_windows, get_window_state
and an element token from that snapshot.

This tool does those steps itself through the ``cua-driver`` command line,
which answers in JSON. The model names the app, the buttons to press and the
text to type; the tool finds the window (opening the app without bringing it
to the front when it is not running), presses each button by its accessible
name, types, and reads the window back. Typed text is checked against what
the app then holds: CUA's character-by-character typing swapped two letters in
one of three Notepad runs, so a mismatch in a field that was empty is set
again directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.core.protocols import ExecutionContext, RiskTier, ToolResult

log = logging.getLogger(__name__)

APP_CONTROL = "app_control"

#: Seconds one cua-driver call may take before the tool gives up on it.
_CALL_TIMEOUT_S = 20.0
#: Seconds a freshly opened app gets to draw its window.
_SETTLE_S = 1.5
#: Labels listed back when a button is not found, so the model can pick one.
_MAX_LABELS = 40
_MAX_READ = 600

#: What people say for a button versus its accessible name in Windows apps.
_SPOKEN_BUTTONS: dict[str, str] = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
    "+": "plus",
    "-": "minus",
    "*": "multiply by",
    "x": "multiply by",
    "×": "multiply by",
    "times": "multiply by",
    "multiply": "multiply by",
    "/": "divide by",
    "÷": "divide by",
    "divide": "divide by",
    "=": "equals",
    "equal": "equals",
    ".": "decimal separator",
    "point": "decimal separator",
    "c": "clear",
    "ce": "clear entry",
    "%": "percent",
    "sqrt": "square root",
    "√": "square root",
    "squared": "square",
    "back": "backspace",
}
_EDITABLE_ROLES = ("document", "edit")
#: Browser programs, whose window titles are page titles.
_BROWSERS = frozenset({"chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "arc"})


class CuaError(RuntimeError):
    """cua-driver answered with an error instead of a result."""


def driver_path() -> str | None:
    """The cua-driver executable, or ``None`` when it is not installed."""
    found = shutil.which("cua-driver")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        default = Path(local) / "Programs" / "Cua" / "cua-driver" / "bin" / "cua-driver.exe"
        if default.is_file():
            return str(default)
    return None


def app_control_tool() -> AppControlTool | None:
    """The tool on Windows with CUA Driver installed, else ``None``."""
    if sys.platform != "win32":
        return None
    exe = driver_path()
    return AppControlTool(exe=exe) if exe else None


async def run_driver(exe: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """One ``cua-driver call <tool>`` with the arguments on stdin; its JSON answer.

    The arguments go on stdin because Windows PowerShell 5.1 strips the quotes
    of a JSON argument. An answer that is not JSON is the driver's error text.
    """
    proc = await asyncio.create_subprocess_exec(
        exe,
        "call",
        tool,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(json.dumps(args).encode("utf-8")), _CALL_TIMEOUT_S
        )
    except TimeoutError:
        proc.kill()
        raise CuaError(f"cua-driver {tool} took longer than {_CALL_TIMEOUT_S:.0f} s") from None
    text = out.decode("utf-8", "replace").strip()
    try:
        value = json.loads(text)
    except ValueError:
        detail = text or err.decode("utf-8", "replace").strip()
        raise CuaError(detail[:300] or f"cua-driver {tool} failed") from None
    if not isinstance(value, dict):
        raise CuaError(f"cua-driver {tool} answered {text[:120]}")
    if value.get("isError") or value.get("error"):
        message = value.get("message") or value.get("error") or text
        raise CuaError(str(message)[:300])
    return value


def _norm(label: str) -> str:
    return " ".join(str(label or "").lower().split())


def find_button(elements: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """The element a spoken button name means: exact label first, then a prefix."""
    wanted = _norm(name)
    wanted = _SPOKEN_BUTTONS.get(wanted, wanted)
    clickable = [e for e in elements if e.get("element_token") and e.get("label")]
    for e in clickable:
        if _norm(e["label"]) == wanted:
            return e
    for e in clickable:
        if _norm(e["label"]).startswith(wanted + " ") or _norm(e["label"]).startswith(wanted):
            return e
    return None


_OPERATORS = frozenset({"plus", "minus", "multiply by", "divide by"})


def _sum_left_open(pressed: list[str]) -> bool:
    """Whether an operator was pressed and the sum never finished with Equals."""
    labels = [_norm(label) for label in pressed]
    if not any(label in _OPERATORS for label in labels):
        return False
    return labels[-1] != "equals"


def find_editable(elements: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The window's text area: a Document or Edit that takes a value."""
    for role in _EDITABLE_ROLES:
        for e in elements:
            if _norm(e.get("role", "")) == role and e.get("element_token"):
                return e
    return None


def read_out(elements: list[dict[str, Any]]) -> str:
    """What the window shows: its text area, else its text and display labels."""
    editable = find_editable(elements)
    if editable is not None and editable.get("value") is not None:
        return f"Text: {str(editable['value'])[:_MAX_READ]!r}"
    shown = [
        str(e["label"]) for e in elements if _norm(e.get("role", "")) == "text" and e.get("label")
    ]
    return "Shows: " + "; ".join(shown)[:_MAX_READ] if shown else "Nothing readable."


@dataclass
class AppControlTool:
    """Presses buttons and types in a desktop app without taking the user's focus."""

    name: str = APP_CONTROL
    risk_tier: RiskTier = "monitor"
    description: str = (
        "Use a desktop app (Calculator, Notepad, Settings, any open window) in the "
        "background without moving the mouse or taking focus: opens it if needed, "
        "presses buttons by their names in order, types text, and reads back what "
        "the window shows. Prefer it over computer_use for a named app."
    )
    schema: dict[str, Any] | None = None
    exe: str | None = None

    def __post_init__(self) -> None:
        if self.schema is None:
            self.schema = {
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "The app or window title, e.g. 'Calculator', 'Notepad'.",
                    },
                    "press": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Buttons to press in order, by name or symbol, e.g. "
                            "['1', '2', '*', '3', '=']."
                        ),
                    },
                    "type": {
                        "type": "string",
                        "description": "Text to type into the app's text area.",
                    },
                },
                "required": ["app"],
            }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        del ctx
        app = str(args.get("app") or "").strip()
        if not app:
            return ToolResult(success=False, output=None, error="app missing")
        exe = self.exe or (driver_path() if sys.platform == "win32" else None)
        if exe is None:
            return ToolResult(
                success=False,
                output=None,
                error="CUA Driver is not installed, so apps cannot be driven in the background.",
            )
        press = [str(p) for p in (args.get("press") or []) if str(p).strip()]
        text = str(args.get("type") or "")
        try:
            return await self._drive(exe, app, press, text)
        except CuaError as exc:
            log.warning("app_control: %s on %r failed: %s", APP_CONTROL, app, exc)
            return ToolResult(success=False, output=None, error=f"{app}: {exc}")

    async def _drive(self, exe: str, app: str, press: list[str], text: str) -> ToolResult:
        pid, window_id, title = await self._window(exe, app)
        state = await self._state(exe, pid, window_id)
        done: list[str] = []
        for button in press:
            element = find_button(state, button)
            if element is None:
                labels = [str(e["label"]) for e in state if e.get("label")][:_MAX_LABELS]
                pressed = f" after pressing {', '.join(done)}" if done else ""
                return ToolResult(
                    success=False,
                    output=None,
                    error=(
                        f"{title} has no button {button!r}{pressed}. Its controls: "
                        + "; ".join(labels)
                    ),
                )
            await run_driver(
                exe,
                "click",
                {"pid": pid, "window_id": window_id, "element_token": element["element_token"]},
            )
            done.append(str(element["label"]))
        if _sum_left_open(done):
            # Live 2026-10-25 Gemma pressed 1, 2, x, 3 and stopped; the display
            # still read 3 and it answered "36" from its own arithmetic.
            equals = find_button(state, "=")
            if equals is not None:
                await run_driver(
                    exe,
                    "click",
                    {"pid": pid, "window_id": window_id, "element_token": equals["element_token"]},
                )
                done.append(str(equals["label"]))
        if text:
            state = await self._state(exe, pid, window_id)
            await self._type(exe, pid, window_id, state, text)
        state = await self._state(exe, pid, window_id)
        did = f"Pressed {', '.join(done)}. " if done else ""
        typed = f"Typed {text!r}. " if text else ""
        return ToolResult(success=True, output=f"{title}: {did}{typed}{read_out(state)}")

    async def _window(self, exe: str, app: str) -> tuple[int, int, str]:
        """The app's top window, opened without focus when none is running."""
        needle = _norm(app).removesuffix(".exe")
        listed = await run_driver(exe, "list_windows", {})
        match = _match_window(listed.get("windows") or [], needle)
        if match is None:
            launched = await run_driver(exe, "launch_app", {"name": app})
            # launch_app's windows carry no pid; the app's pid is beside them.
            opened = [
                {**w, "pid": launched.get("pid")}
                for w in launched.get("windows") or []
                if isinstance(w, dict)
            ]
            match = _match_window(opened, needle) or next(iter(opened), None)
            if match is None:
                await asyncio.sleep(_SETTLE_S)
                listed = await run_driver(exe, "list_windows", {})
                match = _match_window(listed.get("windows") or [], needle)
        if match is None:
            raise CuaError(f"no window for {app!r} appeared")
        return int(match["pid"]), int(match["window_id"]), str(match.get("title") or app)

    async def _state(self, exe: str, pid: int, window_id: int) -> list[dict[str, Any]]:
        state = await run_driver(exe, "get_window_state", {"pid": pid, "window_id": window_id})
        return [e for e in state.get("elements") or [] if isinstance(e, dict)]

    async def _type(
        self, exe: str, pid: int, window_id: int, state: list[dict[str, Any]], text: str
    ) -> None:
        field = find_editable(state)
        before = str(field.get("value") or "") if field else ""
        await run_driver(exe, "type_text", {"pid": pid, "window_id": window_id, "text": text})
        if field is None:
            return
        after = find_editable(await self._state(exe, pid, window_id))
        if after is None:
            return
        held = str(after.get("value") or "")
        if held == before + text or before or "set_value" not in (after.get("actions") or []):
            return
        # Posted characters arrived out of order ("urn" for "run", live
        # 2026-10-25); a field that was empty can simply be given the text.
        log.info("app_control: typed text came out as %r; setting it directly", held[:60])
        await run_driver(
            exe,
            "set_value",
            {
                "pid": pid,
                "window_id": window_id,
                "element_token": after["element_token"],
                "value": text,
            },
        )


def _match_window(windows: list[dict[str, Any]], needle: str) -> dict[str, Any] | None:
    """The window ``needle`` names: its exact title, its program, then a title word.

    A title word comes last and never matches a browser, so "calculator" with
    the app closed opens it rather than typing into a tab titled "Calculator -
    Google Search".
    """
    usable = [
        w
        for w in windows
        if isinstance(w, dict) and w.get("window_id") is not None and w.get("pid") is not None
    ]
    if not needle:
        return None
    for test in (
        lambda w: _norm(w.get("title", "")) == needle,
        lambda w: _program(w) == needle,
        lambda w: (
            _program(w) not in _BROWSERS
            and re.search(rf"\b{re.escape(needle)}\b", _norm(w.get("title", "")))
        ),
    ):
        for w in usable:
            if test(w):
                return w
    return None


def _program(window: dict[str, Any]) -> str:
    return _norm(window.get("app_name", "")).removesuffix(".exe")
