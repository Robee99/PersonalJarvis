"""The Windows hands of the PC pilot: windows, UI Automation, mouse and keys.

Everything here is a thin call into Win32 or UI Automation; the decisions
(which window, which control, whether a step may run) live in
``jarvis.plugins.tool.pc_pilot``. Input goes through ``SendInput``, the call a
real keyboard and mouse driver makes, so every app sees the same events a
person's hands would send. Text is typed as Unicode characters, never as
virtual keys, so the keyboard layout cannot change what lands.

Windows only. Every import of a Windows package is lazy, so the module loads
anywhere (the tests drive the pilot through a fake desk).
"""

from __future__ import annotations

import ctypes
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Window:
    handle: int
    title: str
    pid: int = 0
    exe: str = ""
    minimized: bool = False


@dataclass
class Control:
    name: str
    role: str
    bounds: tuple[int, int, int, int]  # left, top, width, height
    enabled: bool = True
    focused: bool = False
    password: bool = False
    ref: Any = field(default=None, repr=False, compare=False)

    def center(self) -> tuple[int, int]:
        left, top, width, height = self.bounds
        return left + width // 2, top + height // 2


#: Control types the pilot lists, clickable ones first.
CLICKABLE_ROLES = (
    "Button", "SplitButton", "Hyperlink", "MenuItem", "ListItem", "TabItem",
    "TreeItem", "CheckBox", "RadioButton", "ComboBox", "DataItem", "Edit",
    "Slider", "Spinner",
)
READABLE_ROLES = ("Document", "Text", "Header", "HeaderItem", "StatusBar", "ToolBar")

_VK: dict[str, int] = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B,
    "windows": 0x5B, "cmd": 0x5B, "enter": 0x0D, "return": 0x0D, "tab": 0x09,
    "esc": 0x1B, "escape": 0x1B, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "space": 0x20, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22,
    "pgdn": 0x22, "insert": 0x2D, "printscreen": 0x2C, "prtsc": 0x2C,
    "capslock": 0x14, "menu": 0x5D, "apps": 0x5D, "volumeup": 0xAF,
    "volumedown": 0xAE, "mute": 0xAD, "volumemute": 0xAD, "playpause": 0xB3,
    "play": 0xB3, "pause": 0xB3, "nexttrack": 0xB0, "next": 0xB0,
    "prevtrack": 0xB1, "previous": 0xB1, "stop": 0xB2,
    ",": 0xBC, ".": 0xBE, "-": 0xBD, "=": 0xBB, "/": 0xBF, ";": 0xBA,
    "'": 0xDE, "[": 0xDB, "]": 0xDD, "\\": 0xDC, "`": 0xC0, "plus": 0xBB,
    "minus": 0xBD,
}
_VK.update({f"f{n}": 0x6F + n for n in range(1, 13)})
_EXTENDED = frozenset({0x2E, 0x26, 0x28, 0x25, 0x27, 0x24, 0x23, 0x21, 0x22, 0x2D, 0x5B, 0x5D})


def key_codes(combo: str) -> list[int]:
    """Virtual-key codes for ``ctrl+shift+s``-style text; raises ValueError on a stranger."""
    codes: list[int] = []
    for part in combo.replace(" ", "").split("+"):
        low = part.casefold()
        if not low:
            continue
        if low in _VK:
            codes.append(_VK[low])
        elif len(low) == 1 and (low.isalnum()):
            codes.append(ord(low.upper()))
        else:
            raise ValueError(f"Unknown key {part!r}.")
    if not codes:
        raise ValueError("No key given.")
    return codes


#: Text at least this long goes in by paste rather than key by key.
_PASTE_FROM = 120


class WinDesk:
    """The real desktop. Every method may raise ``OSError`` on a Win32 failure."""

    def __init__(self) -> None:
        from ctypes import wintypes  # noqa: PLC0415

        self._w = wintypes
        # A private user32: argtypes bound here must never leak into the
        # shared ctypes.windll other libraries use (see window_state.py).
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._dwm = ctypes.WinDLL("dwmapi")
        self._own_pid = os.getpid()
        self._define_input()

    # -- windows ------------------------------------------------------------
    def windows(self) -> list[Window]:
        """Visible, titled app windows, front to back, never Jarvis's own."""
        w = self._w
        user32 = self._user32
        out: list[Window] = []
        proc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

        def visit(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd) or self._cloaked(hwnd):
                return True
            ex = user32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF
            if ex & 0x80 and not ex & 0x40000:  # a tool window that is no app window
                return True
            if ex & 0x08000000:  # WS_EX_NOACTIVATE: an overlay such as a cursor halo
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            pid = w.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if self._is_own(int(pid.value)) or buf.value == "Program Manager":
                return True
            out.append(Window(
                handle=int(hwnd),
                title=buf.value,
                pid=int(pid.value),
                exe=_exe_name(int(pid.value)),
                minimized=bool(user32.IsIconic(hwnd)),
            ))
            return True

        user32.EnumWindows.argtypes = [proc, w.LPARAM]
        user32.EnumWindows(proc(visit), 0)
        return [win for win in out if win.exe.casefold() not in _SHELL_EXES]

    def foreground(self) -> int:
        self._user32.GetForegroundWindow.restype = self._w.HWND
        return int(self._user32.GetForegroundWindow() or 0)

    def is_window(self, handle: int) -> bool:
        return bool(self._user32.IsWindow(handle))

    def activate(self, win: Window) -> bool:
        from jarvis.platform import window_state  # noqa: PLC0415

        if win.minimized or self._user32.IsIconic(win.handle):
            self._user32.ShowWindow(win.handle, 9)  # SW_RESTORE
        ok, _ = window_state.raise_window(
            window_state.WindowInfo(title=win.title, handle=win.handle, pid=win.pid)
        )
        time.sleep(0.15)
        return ok or self.foreground() == win.handle

    def window_op(self, win: Window, op: str) -> None:
        if op == "close":
            self._user32.PostMessageW(win.handle, 0x0010, 0, 0)  # WM_CLOSE
            return
        show = {"minimize": 6, "maximize": 3, "restore": 9}[op]
        self._user32.ShowWindow(win.handle, show)

    # -- UI Automation --------------------------------------------------------
    def controls(self, win: Window, limit: int = 1500) -> list[Control]:
        """Named, on-screen controls of ``win`` and of its open popups, popups first.

        A right-click menu, a dropdown, Chrome's own menu and a Save dialog
        are separate windows of the same program: live 2026-10-26 the
        pilot read Explorer while its context menu sat on top unread.
        """
        out: list[Control] = []
        for handle in [*self._popups(win), win.handle]:
            try:
                out.extend(self._controls_of(handle, limit - len(out)))
            except Exception:  # noqa: BLE001 - a popup that closed mid-read; the rest still count
                if handle == win.handle:
                    raise
            if len(out) >= limit:
                break
        return out

    def _popups(self, win: Window) -> list[int]:
        """Visible popup windows of ``win``'s program that sit above it."""
        w = self._w
        user32 = self._user32
        found: list[int] = []
        proc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

        def visit(hwnd: int, _lparam: int) -> bool:
            if int(hwnd) == win.handle:
                return False  # everything after this sits below the window
            if not user32.IsWindowVisible(hwnd) or self._cloaked(hwnd):
                return True
            pid = w.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) != win.pid:
                return True
            style = user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF
            cls = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, cls, 128)
            if cls.value in _NOT_POPUPS or not (style & 0x80000000 or cls.value == "#32768"):
                return True
            found.append(int(hwnd))
            return True

        user32.EnumWindows.argtypes = [proc, w.LPARAM]
        user32.EnumWindows(proc(visit), 0)
        return found[:4]

    def _controls_of(self, handle: int, limit: int) -> list[Control]:
        """Named, on-screen controls under one window handle, in one cached UIA query."""
        from pywinauto.uia_defines import IUIA  # noqa: PLC0415

        from jarvis.core.win32_dpi import per_monitor_dpi_context  # noqa: PLC0415

        uia = IUIA()
        dll, client = uia.UIA_dll, uia.iuia
        roles = [*CLICKABLE_ROLES, *READABLE_ROLES]
        conditions = [
            client.CreatePropertyCondition(
                dll.UIA_ControlTypePropertyId, uia.known_control_types[r]
            )
            for r in roles
            if r in uia.known_control_types
        ]
        cache = client.CreateCacheRequest()
        for prop in (
            dll.UIA_NamePropertyId, dll.UIA_ControlTypePropertyId,
            dll.UIA_BoundingRectanglePropertyId, dll.UIA_IsEnabledPropertyId,
            dll.UIA_IsOffscreenPropertyId, dll.UIA_IsPasswordPropertyId,
            dll.UIA_HasKeyboardFocusPropertyId,
        ):
            cache.AddProperty(prop)
        with per_monitor_dpi_context():
            root = client.ElementFromHandle(handle)
            found = root.FindAllBuildCache(
                dll.TreeScope_Descendants, client.CreateOrConditionFromArray(conditions), cache
            )
            count = min(int(found.Length), limit)
            out: list[Control] = []
            for i in range(count):
                try:
                    el = found.GetElement(i)
                    if bool(el.CachedIsOffscreen):
                        continue
                    name = " ".join(str(el.CachedName or "").split())
                    rect = el.CachedBoundingRectangle
                    width = int(rect.right) - int(rect.left)
                    height = int(rect.bottom) - int(rect.top)
                    role = str(uia.known_control_type_ids.get(int(el.CachedControlType), ""))
                    if width <= 0 or height <= 0 or (not name and role != "Edit"):
                        continue
                    out.append(Control(
                        name=name,
                        role=role,
                        bounds=(int(rect.left), int(rect.top), width, height),
                        enabled=bool(el.CachedIsEnabled),
                        focused=bool(el.CachedHasKeyboardFocus),
                        password=bool(el.CachedIsPassword),
                        ref=el,
                    ))
                except Exception:  # noqa: BLE001, S112 - a control that vanished mid-read is skipped
                    continue
        return out

    def invoke(self, control: Control) -> bool:
        """Press ``control`` through its own UIA pattern; False when it has none."""
        from pywinauto.uia_defines import IUIA, get_elem_interface  # noqa: PLC0415

        dll = IUIA().UIA_dll
        el = control.ref
        if el is None:
            return False
        for pattern_id, name, call in (
            (dll.UIA_InvokePatternId, "Invoke", "Invoke"),
            (dll.UIA_TogglePatternId, "Toggle", "Toggle"),
            (dll.UIA_SelectionItemPatternId, "SelectionItem", "Select"),
            (dll.UIA_ExpandCollapsePatternId, "ExpandCollapse", "Expand"),
        ):
            try:
                if not el.GetCurrentPattern(pattern_id):
                    continue
                getattr(get_elem_interface(el, name), call)()
                return True
            except Exception:  # noqa: BLE001, S112 - try the next pattern, then the mouse
                continue
        return False

    def focused_text(self) -> str | None:
        """What the focused field holds; ``None`` for a password or no field."""
        from pywinauto.uia_defines import IUIA, get_elem_interface  # noqa: PLC0415

        uia = IUIA()
        dll = uia.UIA_dll
        try:
            el = uia.iuia.GetFocusedElement()
            if el is None or bool(el.CurrentIsPassword):
                return None
            if el.GetCurrentPattern(dll.UIA_ValuePatternId):
                return str(get_elem_interface(el, "Value").CurrentValue or "")
            if el.GetCurrentPattern(dll.UIA_TextPatternId):
                text = get_elem_interface(el, "Text").DocumentRange.GetText(4000)
                return str(text or "")
        except Exception:  # noqa: BLE001 - an unreadable field just reports nothing
            log.debug("pilot: focused text unreadable", exc_info=True)
        return None

    # -- input ----------------------------------------------------------------
    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None:
        from jarvis.core.win32_dpi import per_monitor_dpi_context  # noqa: PLC0415

        down, up = {"left": (0x2, 0x4), "right": (0x8, 0x10), "middle": (0x20, 0x40)}[button]
        with per_monitor_dpi_context():
            self._user32.SetCursorPos(int(x), int(y))
            time.sleep(0.05)
            for _ in range(max(1, count)):
                self._send([self._mouse(down), self._mouse(up)])
                time.sleep(0.06)

    def scroll(self, x: int, y: int, notches: int) -> None:
        from jarvis.core.win32_dpi import per_monitor_dpi_context  # noqa: PLC0415

        with per_monitor_dpi_context():
            self._user32.SetCursorPos(int(x), int(y))
            time.sleep(0.05)
            self._send([self._mouse(0x0800, (120 * notches) & 0xFFFFFFFF)])

    def press(self, combo: str) -> None:
        codes = key_codes(combo)
        events = [self._key(c, up=False) for c in codes]
        events += [self._key(c, up=True) for c in reversed(codes)]
        self._send(events)

    def type_text(self, text: str) -> None:
        """Type ``text`` into the focused control, key by key like a keyboard.

        Each character goes as the real key that makes it on the current
        layout (with Shift when it needs it). Unicode packet keys are only the
        fallback for a character no key makes: live 2026-10-26 Windows 11
        Notepad read a run of packet keys as repeats of one character
        ("hello from iiiiiiiiilot"). Long text goes in by paste, with the
        clipboard's own text put back and the paste kept out of history.
        """
        if len(text) >= _PASTE_FROM and self._paste(text):
            return
        self._user32.VkKeyScanW.restype = ctypes.c_short
        for ch in text:
            if ch == "\r":
                continue
            if ch in "\n\t":
                vk = 0x0D if ch == "\n" else 0x09
                self._send([self._key(vk, up=False), self._key(vk, up=True)])
                time.sleep(0.008)
                continue
            scan = int(self._user32.VkKeyScanW(ord(ch))) if len(ch.encode("utf-16-le")) == 2 else -1
            vk, mods = scan & 0xFF, (scan >> 8) & 0xFF
            if scan != -1 and vk != 0xFF and not mods & 0x6:  # no Ctrl/Alt (AltGr) chars
                held = [0x10] if mods & 0x1 else []
                self._send(
                    [self._key(m, up=False) for m in held]
                    + [self._key(vk, up=False), self._key(vk, up=True)]
                    + [self._key(m, up=True) for m in held]
                )
                time.sleep(0.008)
                continue
            data = ch.encode("utf-16-le")
            units = [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
            self._send([e for u in units
                        for e in (self._unicode(u, up=False), self._unicode(u, up=True))])
            time.sleep(0.04)

    def _paste(self, text: str) -> bool:
        import win32clipboard as clip  # noqa: PLC0415

        text_formats = {1, 7, 13, 16}  # CF_TEXT, CF_OEMTEXT, CF_UNICODETEXT, CF_LOCALE
        try:
            clip.OpenClipboard()
        except Exception:  # noqa: BLE001 - another program holds the clipboard: type keys instead
            return False
        try:
            formats: list[int] = []
            fmt = clip.EnumClipboardFormats(0)
            while fmt:
                formats.append(fmt)
                fmt = clip.EnumClipboardFormats(fmt)
            if any(f not in text_formats for f in formats if f < 0xC000):
                return False
            saved = clip.GetClipboardData(13) if 13 in formats else None
            clip.EmptyClipboard()
            clip.SetClipboardData(13, text)
            for name in ("ExcludeClipboardContentFromMonitorProcessing",
                         "CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"):
                clip.SetClipboardData(clip.RegisterClipboardFormat(name), b"\0\0\0\0")
        finally:
            clip.CloseClipboard()
        self.press("ctrl+v")
        time.sleep(0.25)
        try:
            clip.OpenClipboard()
            clip.EmptyClipboard()
            if saved is not None:
                clip.SetClipboardData(13, saved)
            clip.CloseClipboard()
        except Exception:  # noqa: BLE001 - the text is in; only the old clipboard is lost
            log.debug("pilot: clipboard restore failed", exc_info=True)
        return True

    # -- internals ------------------------------------------------------------
    def _is_own(self, pid: int) -> bool:
        if pid == self._own_pid:
            return True
        try:
            import psutil  # noqa: PLC0415

            return int(psutil.Process(pid).ppid()) == self._own_pid
        except Exception:  # noqa: BLE001 - no psutil or a gone pid: not ours
            return False

    def _cloaked(self, hwnd: int) -> bool:
        value = ctypes.c_int(0)
        try:
            self._dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(value), ctypes.sizeof(value))
        except OSError:  # no DWM answer: treat the window as shown
            return False
        return bool(value.value)

    def _define_input(self) -> None:
        w = self._w
        ulong_ptr = ctypes.c_size_t

        class KeybdInput(ctypes.Structure):
            _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                        ("time", w.DWORD), ("dwExtraInfo", ulong_ptr)]

        class MouseInput(ctypes.Structure):
            _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD),
                        ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", ulong_ptr)]

        class HardwareInput(ctypes.Structure):
            _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]

        class InputUnion(ctypes.Union):
            _fields_ = [("ki", KeybdInput), ("mi", MouseInput), ("hi", HardwareInput)]

        class Input(ctypes.Structure):
            _fields_ = [("type", w.DWORD), ("u", InputUnion)]

        self._Input, self._Keybd, self._Mouse = Input, KeybdInput, MouseInput
        self._user32.SendInput.argtypes = [w.UINT, ctypes.POINTER(Input), ctypes.c_int]
        self._user32.SendInput.restype = w.UINT

    def _key(self, vk: int, *, up: bool) -> Any:
        flags = (0x2 if up else 0) | (0x1 if vk in _EXTENDED else 0)
        event = self._Input(type=1)
        event.u.ki = self._Keybd(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
        return event

    def _unicode(self, unit: int, *, up: bool) -> Any:
        event = self._Input(type=1)
        event.u.ki = self._Keybd(wVk=0, wScan=unit, dwFlags=0x4 | (0x2 if up else 0), time=0,
                                 dwExtraInfo=0)
        return event

    def _mouse(self, flags: int, data: int = 0) -> Any:
        event = self._Input(type=0)
        event.u.mi = self._Mouse(dx=0, dy=0, mouseData=data, dwFlags=flags, time=0, dwExtraInfo=0)
        return event

    def _send(self, events: list[Any]) -> None:
        if not events:
            return
        array = (self._Input * len(events))(*events)
        sent = self._user32.SendInput(len(events), array, ctypes.sizeof(self._Input))
        if sent != len(events):
            # UIPI: an elevated window (Task Manager, an installer) refuses
            # input from an unelevated process.
            raise OSError(
                "Windows refused the input; the window probably runs as administrator."
            )


#: Window classes of a program that are never its popup (Explorer's taskbar and desktop).
_NOT_POPUPS = frozenset({"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW",
                         "tooltips_class32", "NotifyIconOverflowWindow"})

#: Desktop shell surfaces that are never a target app window.
_SHELL_EXES = frozenset({"textinputhost.exe", "shellexperiencehost.exe", "searchhost.exe",
                         "startmenuexperiencehost.exe", "lockapp.exe"})


def _exe_name(pid: int) -> str:
    try:
        import psutil  # noqa: PLC0415

        return str(psutil.Process(pid).name() or "")
    except Exception:  # noqa: BLE001 - a protected or gone process has no name to show
        return ""


__all__ = ["CLICKABLE_ROLES", "READABLE_ROLES", "Control", "WinDesk", "Window", "key_codes"]
