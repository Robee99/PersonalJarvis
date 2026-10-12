"""laptop_power: the exact ATKACPI bytes, the power-mode GUIDs and honest refusals.

The transport and the powrprof calls are fakes that record what would have
reached the driver; nothing here touched a real laptop.
"""

from __future__ import annotations

import struct
import sys
from types import SimpleNamespace

import pytest

from jarvis.platform import laptop_power
from jarvis.platform.laptop_power import (
    AsusControl,
    LaptopPowerUnavailable,
    PowerOverlayApi,
    WindowsPowerMode,
)
from jarvis.plugins.tool.laptop_power import LaptopPowerTool

DSTS, DEVS = 0x53545344, 0x53564544
MODE, LIMIT = 0x00120075, 0x00120057


class _Atk:
    """A fake ATKACPI driver: answers DSTS from ``status`` and DEVS with ``devs``."""

    def __init__(self, status: dict[int, int], devs: int = 1) -> None:
        self.status = status
        self.devs = devs
        self.requests: list[tuple[int, int, int, int]] = []

    def __call__(self, request: bytes) -> bytes:
        method, length, device, value = struct.unpack("<IIII", request)
        self.requests.append((method, length, device, value))
        answer = self.status.get(device, 0) if method == DSTS else self.devs
        return struct.pack("<IIII", answer, 0, 0, 0)


class _Overlay:
    def __init__(self, current: str) -> None:
        self.current = current
        self.sets: list[str] = []

    def api(self) -> PowerOverlayApi:
        def set_(guid: str) -> None:
            self.sets.append(guid)
            self.current = guid

        return PowerOverlayApi(get=lambda: self.current, set=set_)


def _tool(atk: _Atk | None = None, overlay: _Overlay | None = None, running: bool = False):
    return LaptopPowerTool(
        windows=WindowsPowerMode(
            api=(overlay or _Overlay(laptop_power.WINDOWS_POWER_MODES["balanced"])).api()
        ),
        asus=AsusControl(transport=atk) if atk is not None else None,
        armoury_running=lambda: running,
    )


async def test_turbo_sends_the_documented_devs_request() -> None:
    atk = _Atk({MODE: 0x00010000})

    result = await _tool(atk).execute(
        {"action": "set_asus_mode", "mode": "turbo"}, SimpleNamespace()
    )

    assert result.success, result.error
    assert atk.requests == [(DSTS, 8, MODE, 0), (DEVS, 8, MODE, 1)]


async def test_armoury_crate_overwrite_is_mentioned() -> None:
    atk = _Atk({MODE: 0x00010000})

    result = await _tool(atk, running=True).execute(
        {"action": "set_asus_mode", "mode": "silent"}, SimpleNamespace()
    )

    assert atk.requests[-1] == (DEVS, 8, MODE, 2)
    assert "Armoury Crate is running" in result.output


async def test_mode_the_laptop_does_not_report_is_never_written() -> None:
    atk = _Atk({})

    result = await _tool(atk).execute(
        {"action": "set_asus_mode", "mode": "turbo"}, SimpleNamespace()
    )

    assert not result.success
    assert [r[0] for r in atk.requests] == [DSTS]


async def test_refused_write_is_an_error() -> None:
    atk = _Atk({MODE: 0x00010000}, devs=0)

    result = await _tool(atk).execute(
        {"action": "set_asus_mode", "mode": "turbo"}, SimpleNamespace()
    )

    assert result.error == "The change failed: The laptop refused the turbo mode."


@pytest.mark.parametrize("percent", [20, 59, 101])
async def test_charge_limit_outside_60_to_100_is_refused_before_the_driver(percent: int) -> None:
    atk = _Atk({LIMIT: 0x00010050})

    result = await _tool(atk).execute(
        {"action": "set_charge_limit", "percent": percent}, SimpleNamespace()
    )

    assert not result.success
    assert atk.requests == []


async def test_charge_limit_80() -> None:
    atk = _Atk({LIMIT: 0x00010064})

    result = await _tool(atk).execute(
        {"action": "set_charge_limit", "percent": 80}, SimpleNamespace()
    )

    assert result.output == "Battery charge limit set to 80 %."
    assert atk.requests[-1] == (DEVS, 8, LIMIT, 80)


async def test_windows_mode_uses_the_slider_guids() -> None:
    overlay = _Overlay("00000000-0000-0000-0000-000000000000")

    result = await _tool(overlay=overlay).execute(
        {"action": "set_windows_mode", "mode": "performance"}, SimpleNamespace()
    )

    assert result.output == "Windows power mode set to performance."
    assert overlay.sets == ["ded574b5-45a0-4f42-8737-46345c09c238"]


async def test_status_reads_both_without_writing() -> None:
    atk = _Atk({MODE: 0x00010000, LIMIT: 0x00010050})
    overlay = _Overlay("961cc777-2547-4f9d-8174-7d86181b8a7a")

    result = await _tool(atk, overlay).execute({"action": "status"}, SimpleNamespace())

    assert result.output == (
        "Windows power mode: efficiency. ASUS controls: available, charge limit 80 %."
    )
    assert all(r[0] == DSTS for r in atk.requests)
    assert overlay.sets == []


async def test_status_on_a_non_asus_laptop_says_so() -> None:
    def no_driver(_request: bytes) -> bytes:
        raise LaptopPowerUnavailable("This laptop has no ASUS System Control Interface")

    tool = LaptopPowerTool(
        windows=WindowsPowerMode(api=_Overlay("x").api()),
        asus=AsusControl(transport=no_driver),
        armoury_running=lambda: False,
    )

    result = await tool.execute({"action": "status"}, SimpleNamespace())

    assert result.success
    assert "custom (x)" in result.output
    assert "ASUS controls: unavailable (This laptop has no ASUS" in result.output


async def test_off_windows_everything_is_honestly_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    tool = LaptopPowerTool()
    status = await tool.execute({"action": "status"}, SimpleNamespace())
    turbo = await tool.execute({"action": "set_asus_mode", "mode": "turbo"}, SimpleNamespace())

    assert "only on Windows" in status.output
    assert turbo.error == "Armoury Crate settings exist only on Windows."


async def test_unknown_action_and_mode() -> None:
    tool = _tool(_Atk({MODE: 0x00010000}))

    bad_action = await tool.execute({"action": "fan_curve"}, SimpleNamespace())
    bad_mode = await tool.execute(
        {"action": "set_windows_mode", "mode": "turbo"}, SimpleNamespace()
    )

    assert "Unknown action" in bad_action.error
    assert "Unknown power mode 'turbo'" in bad_mode.error


def test_guid_round_trip_matches_windows_byte_order() -> None:
    text = "ded574b5-45a0-4f42-8737-46345c09c238"
    guid = laptop_power._GUID.from_text(text)
    assert guid.Data1 == 0xDED574B5 and guid.Data2 == 0x45A0 and guid.Data3 == 0x4F42
    assert guid.text() == text
