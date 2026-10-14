"""``ensure_normal_process_priority`` — the BUG-204-amplifier repair.

The old autostart Scheduled Task registered without ``-Priority`` and Task
Scheduler's default (7) started the whole tree BelowNormal. The repair must
only ever RAISE BelowNormal/Idle to Normal, never demote, and must be a quiet
no-op off Windows (macOS/Linux login launches already start at normal
priority).
"""

from __future__ import annotations

import sys
from ctypes import wintypes

import pytest

from jarvis.core import process_utils


class _FakeWin32Function:
    """Callable Win32 function fake that retains ctypes signature metadata."""

    def __init__(self, result: int, *, record: list[tuple] | None = None) -> None:
        self.result = result
        self.record = record
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        if self.record is not None:
            self.record.append(args)
        return self.result


class _FakeKernel32:
    """Capability fake for the Win32 calls used by the priority repair."""

    def __init__(self, current_class: int, set_ok: bool = True) -> None:
        self.calls: list[tuple] = []
        self.GetCurrentProcess = _FakeWin32Function(-1)  # noqa: N815
        self.GetPriorityClass = _FakeWin32Function(current_class)  # noqa: N815
        self.SetPriorityClass = _FakeWin32Function(1 if set_ok else 0, record=self.calls)  # noqa: N815


class _FakeCtypes:
    def __init__(self, kernel32: _FakeKernel32) -> None:
        self.wintypes = wintypes
        self.WinDLL = lambda _name, **_kwargs: kernel32


def _run_with(monkeypatch: pytest.MonkeyPatch, kernel32: _FakeKernel32) -> str | None:
    monkeypatch.setattr(process_utils.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "ctypes", _FakeCtypes(kernel32))
    return process_utils.ensure_normal_process_priority()


def test_off_windows_is_a_quiet_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_utils.sys, "platform", "linux")
    assert process_utils.ensure_normal_process_priority() is None


def test_raises_below_normal_to_normal_with_pointer_sized_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel32 = _FakeKernel32(current_class=0x00004000)  # BELOW_NORMAL
    detail = _run_with(monkeypatch, kernel32)
    assert detail == "BelowNormal -> Normal"
    assert kernel32.calls == [(-1, 0x00000020)]  # pseudo-handle and NORMAL
    assert kernel32.GetCurrentProcess.argtypes == []
    assert kernel32.GetCurrentProcess.restype is wintypes.HANDLE
    assert kernel32.GetPriorityClass.argtypes == [wintypes.HANDLE]
    assert kernel32.GetPriorityClass.restype is wintypes.DWORD
    assert kernel32.SetPriorityClass.argtypes == [wintypes.HANDLE, wintypes.DWORD]
    assert kernel32.SetPriorityClass.restype is wintypes.BOOL


def test_raises_idle_to_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel32 = _FakeKernel32(current_class=0x00000040)  # IDLE
    detail = _run_with(monkeypatch, kernel32)
    assert detail == "Idle -> Normal"
    assert kernel32.calls == [(-1, 0x00000020)]


def test_never_touches_a_normal_or_higher_class(monkeypatch: pytest.MonkeyPatch) -> None:
    for cls in (0x00000020, 0x00008000, 0x00000080):  # NORMAL, ABOVE_NORMAL, HIGH
        kernel32 = _FakeKernel32(current_class=cls)
        assert _run_with(monkeypatch, kernel32) is None
        assert kernel32.calls == []


def test_failed_set_is_reported_as_nothing_done(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel32 = _FakeKernel32(current_class=0x00004000, set_ok=False)
    assert _run_with(monkeypatch, kernel32) is None
