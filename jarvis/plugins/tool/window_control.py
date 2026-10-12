"""window_control tool: minimize, maximize, restore, focus or close a window.

"Minimize Notepad", "maximize Chrome", "bring Spotify back", "close the
calculator". Hermes's Windows MCP can launch, resize and switch, but has no
minimize, maximize or restore; asked to maximize Notepad, the model resized it
to 1920x1080 and said it was full screen while the window stayed normal (live
2026-10-12). This tool sets the real window state through Win32 and reads the
state back, so the answer says what the window is now.

The window is found by its title or its program name ("chrome" finds
"YouTube - Google Chrome" and chrome.exe), visible top-level windows only.
Close asks the program to close (WM_CLOSE), the same as clicking X: an editor
with unsaved work still asks to save.

Risk tier: monitor. Every action is reversible except close, which the
program itself guards the way it guards the X button.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)

_ACTIONS = ("list", "focus", "minimize", "maximize", "restore", "close")
_SW = {"minimize": 6, "maximize": 3, "restore": 9}
_WM_CLOSE = 0x0010


def _process_name(pid: int | None) -> str:
    if not pid:
        return ""
    try:
        import psutil  # noqa: PLC0415

        return (psutil.Process(pid).name() or "").lower().removesuffix(".exe")
    except Exception:  # noqa: BLE001 - a vanished or protected process has no name
        return ""


def _candidates(target: str, *, program_only: bool = False) -> list[Any]:
    from jarvis.platform.window_state import list_windows  # noqa: PLC0415

    needle = target.strip().lower()
    titled = [w for w in list_windows() if (w.title or "").strip() and w.handle]
    if not needle:
        return titled
    by_title = [] if program_only else [w for w in titled if needle in w.title.lower()]
    by_program = [w for w in titled if needle.replace(" ", "") in _process_name(w.pid)]
    seen: set[int] = set()
    out = []
    for w in by_title + by_program:
        if w.handle not in seen:
            seen.add(w.handle)
            out.append(w)
    return out


def _user32(ctypes: Any) -> Any:
    """A private user32 with pointer-sized handles (never the shared windll)."""
    from ctypes import wintypes  # noqa: PLC0415

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    for name in ("IsWindow", "IsIconic", "IsZoomed"):
        getattr(user32, name).argtypes = [wintypes.HWND]
        getattr(user32, name).restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    return user32


def _state(user32: Any, hwnd: int) -> str:
    if not user32.IsWindow(hwnd):
        return "closed"
    if user32.IsIconic(hwnd):
        return "minimized"
    if user32.IsZoomed(hwnd):
        return "maximized"
    return "normal"


@dataclass(frozen=True)
class WindowResult:
    """The window acted on and its state read back afterwards."""

    title: str
    state: str


def change(action: str, target: str, *, program_only: bool = False) -> WindowResult:
    """Do ``action`` (not list) on the first window matching ``target``.

    ``program_only`` matches the program name alone, never title text, for a
    caller that acts on a spoken word without a model checking the match.
    """
    if os.name != "nt":
        raise RuntimeError("Window control is only available on Windows.")
    import ctypes  # noqa: PLC0415

    from jarvis.platform.window_state import _force_foreground_windows  # noqa: PLC0415

    if not target.strip():
        raise ValueError("Say which window, by its title or program name.")
    windows = _candidates(target, program_only=program_only)
    if not windows:
        raise LookupError(f"No open window matches '{target}'.")
    win = windows[0]
    hwnd = int(win.handle)
    user32 = _user32(ctypes)
    if action == "focus":
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, _SW["restore"])
        _force_foreground_windows(hwnd)
    elif action == "close":
        user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
        for _ in range(20):
            if not user32.IsWindow(hwnd):
                break
            time.sleep(0.1)
    else:
        user32.ShowWindow(hwnd, _SW[action])
        if action != "minimize":
            _force_foreground_windows(hwnd)
        time.sleep(0.15)
    return WindowResult(win.title, _state(user32, hwnd))


def apply(action: str, target: str) -> str:
    """Do ``action`` on the first window matching ``target``; say its state after."""
    if action == "list":
        if os.name != "nt":
            raise RuntimeError("Window control is only available on Windows.")
        windows = _candidates(target)
        if not windows:
            return "No open windows."
        return "Open windows: " + "; ".join(w.title for w in windows[:25])
    result = change(action, target)
    if action == "close" and result.state != "closed":
        return f"Asked '{result.title}' to close; it is still open (it may be asking to save)."
    return f"'{result.title}' is now {result.state}."


class WindowControlTool:
    name: str = "window_control"
    risk_tier: str = "monitor"
    description: str = (
        "Minimizes, maximizes, restores, focuses or closes an open window, found "
        "by title or program name (e.g. 'notepad', 'chrome'); list shows the open "
        "windows. Reports the window's real state afterwards."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "window": {
                "type": "string",
                "description": "Title text or program name; not needed for list",
            },
        },
        "required": ["action"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        del ctx
        action = str((args or {}).get("action") or "")
        if action not in _ACTIONS:
            return ToolResult(
                success=False,
                output=None,
                error=f"Unknown action {action!r}. Allowed: {', '.join(_ACTIONS)}",
            )
        target = str((args or {}).get("window") or "")
        try:
            output = await asyncio.to_thread(apply, action, target)
        except (LookupError, ValueError, RuntimeError) as exc:
            return ToolResult(success=False, output=None, error=str(exc))
        except OSError as exc:
            log.warning("window_control: %s failed", action, exc_info=True)
            return ToolResult(success=False, output=None, error=f"The window change failed: {exc}")
        return ToolResult(success=True, output=output)
