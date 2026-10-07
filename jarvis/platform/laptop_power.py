"""Laptop power controls: the Windows power mode and, on ASUS laptops, the
operating mode and battery charge limit that Armoury Crate sets.

Armoury Crate has no public API. Like every open tool that controls these
settings, this module talks to the "ASUS System Control Interface" driver
directly: ``\\\\.\\ATKACPI`` with one DeviceIoControl call per request. The
buffer layout and device IDs are hardware facts, documented the same way by
atrofac (MIT/Apache-2.0), G-Helper and the Linux ``asus-wmi`` driver; no code
was copied from the GPL sources.

Deliberately NOT exposed: fan curves (can overheat the machine), GPU Eco
(kills apps holding the dGPU) and the MUX switch (needs a reboot).

Nothing here runs at import (AP-26). Every Win32 prototype is bound on this
module's PRIVATE ``WinDLL`` instances, never on ``ctypes.windll`` (see
tests/unit/platform/test_win32_shared_windll_isolation.py).
"""

from __future__ import annotations

import ctypes
import logging
import struct
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: Windows 11 power mode (the Settings > Power slider), as overlay scheme GUIDs.
#: "balanced" is the empty overlay, which is how Windows itself reports it.
WINDOWS_POWER_MODES: dict[str, str] = {
    "efficiency": "961cc777-2547-4f9d-8174-7d86181b8a7a",
    "balanced": "00000000-0000-0000-0000-000000000000",
    "performance": "ded574b5-45a0-4f42-8737-46345c09c238",
}

#: ASUS throttle thermal policy values (Armoury Crate's operating mode).
ASUS_MODES: dict[str, int] = {"balanced": 0, "turbo": 1, "silent": 2}

#: Battery charge limits below this are refused: a voice slip must not park the
#: battery at 20 % on a laptop that is then unplugged.
MIN_CHARGE_LIMIT = 60

_ATK_DEVICE = "\\\\.\\ATKACPI"
_ATK_IOCTL = 0x0022240C
_DSTS = 0x53545344  # "DSTS": read a device status
_DEVS = 0x53564544  # "DEVS": set a device status
_PRESENT = 0x00010000
_DEV_PERFORMANCE_MODE = 0x00120075
_DEV_CHARGE_LIMIT = 0x00120057

AtkTransport = Callable[[bytes], bytes]


class LaptopPowerUnavailable(RuntimeError):
    """The control does not exist here; ``str(exc)`` is the honest reason."""


def _atk_request(method: int, device: int, value: int = 0) -> bytes:
    # [method u32][args length u32][device id u32][value u32], little endian.
    return struct.pack("<IIII", method, 8, device, value)


def _win_atk_transport(request: bytes) -> bytes:
    """One DeviceIoControl round trip; the handle is closed on every path."""
    from ctypes import wintypes  # noqa: PLC0415

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    ioctl = kernel32.DeviceIoControl
    ioctl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    ioctl.restype = wintypes.BOOL
    close = kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL

    generic_rw = 0xC0000000
    share_rw = 0x00000003
    open_existing = 3
    invalid = wintypes.HANDLE(-1).value
    handle = create(_ATK_DEVICE, generic_rw, share_rw, None, open_existing, 0, None)
    if not handle or handle == invalid:
        raise LaptopPowerUnavailable(
            "This laptop has no ASUS System Control Interface, so Armoury Crate "
            "settings cannot be changed here."
        )
    try:
        inbuf = ctypes.create_string_buffer(request, len(request))
        outbuf = ctypes.create_string_buffer(16)
        returned = wintypes.DWORD(0)
        ok = ioctl(
            handle, _ATK_IOCTL, inbuf, len(request), outbuf, 16, ctypes.byref(returned), None
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "DeviceIoControl on ATKACPI failed")
        return outbuf.raw
    finally:
        close(handle)


class AsusControl:
    """The two Armoury Crate settings that are safe to change by voice."""

    def __init__(self, transport: AtkTransport | None = None) -> None:
        if transport is None and sys.platform != "win32":
            raise LaptopPowerUnavailable("Armoury Crate settings exist only on Windows.")
        self._transport = transport or _win_atk_transport

    def _status(self, device: int) -> int:
        return struct.unpack_from("<I", self._transport(_atk_request(_DSTS, device)))[0]

    def _set(self, device: int, value: int) -> bool:
        raw = self._transport(_atk_request(_DEVS, device, value))
        return struct.unpack_from("<I", raw)[0] == 1

    def supports(self, device: int) -> bool:
        return bool(self._status(device) & _PRESENT)

    def set_mode(self, mode: str) -> None:
        if mode not in ASUS_MODES:
            raise ValueError(f"Unknown ASUS mode {mode!r}. Allowed: {', '.join(ASUS_MODES)}")
        if not self.supports(_DEV_PERFORMANCE_MODE):
            raise LaptopPowerUnavailable("This ASUS laptop does not report an operating mode.")
        if not self._set(_DEV_PERFORMANCE_MODE, ASUS_MODES[mode]):
            raise OSError(f"The laptop refused the {mode} mode.")

    def charge_limit(self) -> int | None:
        raw = self._status(_DEV_CHARGE_LIMIT)
        if not raw & _PRESENT:
            return None
        value = raw & 0xFF
        return value if 20 <= value <= 100 else None

    def set_charge_limit(self, percent: int) -> None:
        if not MIN_CHARGE_LIMIT <= percent <= 100:
            raise ValueError(f"Charge limit must be between {MIN_CHARGE_LIMIT} and 100 %.")
        if not self.supports(_DEV_CHARGE_LIMIT):
            raise LaptopPowerUnavailable("This laptop does not report a battery charge limit.")
        if not self._set(_DEV_CHARGE_LIMIT, percent):
            raise OSError(f"The laptop refused the {percent} % charge limit.")


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_text(cls, text: str) -> _GUID:
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)

    def text(self) -> str:
        return str(uuid.UUID(bytes_le=bytes(self)))


@dataclass(frozen=True)
class PowerOverlayApi:
    """``powrprof`` overlay calls; swapped for a fake in tests."""

    get: Callable[[], str]
    set: Callable[[str], None]


def _win_overlay_api() -> PowerOverlayApi:
    powrprof = ctypes.WinDLL("powrprof", use_last_error=True)
    try:
        get_fn = powrprof.PowerGetEffectiveOverlayScheme
        set_fn = powrprof.PowerSetActiveOverlayScheme
    except AttributeError as exc:
        raise LaptopPowerUnavailable("This Windows version has no power mode slider API.") from exc
    get_fn.argtypes = [ctypes.POINTER(_GUID)]
    get_fn.restype = ctypes.c_uint32
    set_fn.argtypes = [_GUID]
    set_fn.restype = ctypes.c_uint32

    def get() -> str:
        guid = _GUID()
        code = get_fn(ctypes.byref(guid))
        if code:
            raise OSError(code, "PowerGetEffectiveOverlayScheme failed")
        return guid.text()

    def set_(text: str) -> None:
        code = set_fn(_GUID.from_text(text))
        if code:
            raise OSError(code, "PowerSetActiveOverlayScheme failed")

    return PowerOverlayApi(get=get, set=set_)


class WindowsPowerMode:
    """The Windows 11 power mode slider (efficiency / balanced / performance)."""

    def __init__(self, api: PowerOverlayApi | None = None) -> None:
        if api is None:
            if sys.platform != "win32":
                raise LaptopPowerUnavailable("The power mode slider exists only on Windows.")
            api = _win_overlay_api()
        self._api = api

    def current(self) -> str:
        guid = self._api.get().lower()
        for name, value in WINDOWS_POWER_MODES.items():
            if value == guid:
                return name
        return f"custom ({guid})"

    def set(self, mode: str) -> None:
        if mode not in WINDOWS_POWER_MODES:
            raise ValueError(
                f"Unknown power mode {mode!r}. Allowed: {', '.join(WINDOWS_POWER_MODES)}"
            )
        self._api.set(WINDOWS_POWER_MODES[mode])


def armoury_crate_running() -> bool:
    """True when Armoury Crate's service would overwrite our change later."""
    if sys.platform != "win32":
        return False
    try:
        import psutil  # noqa: PLC0415

        service = psutil.win_service_get("ArmouryCrateService")
        return service.status() == "running"
    except Exception:  # noqa: BLE001 - absent service or no SCM access both mean "unknown"
        log.debug("laptop_power: Armoury Crate service lookup failed", exc_info=True)
        return False


__all__ = [
    "ASUS_MODES",
    "MIN_CHARGE_LIMIT",
    "WINDOWS_POWER_MODES",
    "AsusControl",
    "LaptopPowerUnavailable",
    "PowerOverlayApi",
    "WindowsPowerMode",
    "armoury_crate_running",
]
