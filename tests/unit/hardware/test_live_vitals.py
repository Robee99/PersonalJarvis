"""live_vitals: what the deck's vitals card shows is what psutil and nvidia-smi said.

psutil and nvidia-smi are fakes; nothing here reads the test machine.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.hardware import detection
from jarvis.hardware.detection import gpu_vitals, live_vitals
from jarvis.ui.web import deck_routes

GIB = 2**30


class _Psutil:
    def __init__(
        self,
        battery: object | None = None,
        freq: object | None = None,
        physical: int | None = 8,
    ) -> None:
        self.battery = battery
        self.freq = freq
        self.physical = physical
        self.intervals: list[float | None] = []

    def cpu_percent(self, interval: float | None = None) -> float:
        self.intervals.append(interval)
        return 37.25

    def cpu_count(self, logical: bool = True) -> int | None:
        return 16 if logical else self.physical

    def cpu_freq(self) -> object | None:
        return self.freq

    def virtual_memory(self) -> SimpleNamespace:
        return SimpleNamespace(total=16 * GIB, available=5.5 * GIB)

    def sensors_battery(self) -> object | None:
        return self.battery


def _smi(output: str):
    calls: list[list[str]] = []

    def run(cmd: list[str], timeout: int = 10) -> str:
        calls.append(cmd)
        return output

    return run, calls


def test_rtx_4060_laptop_reading() -> None:
    run, calls = _smi("NVIDIA GeForce RTX 4060 Laptop GPU, 12, 1536, 8188, 48, 9.87\n")
    ps = _Psutil(
        battery=SimpleNamespace(percent=81.6, power_plugged=True),
        freq=SimpleNamespace(current=3792.4, min=0.0, max=5100.0),
    )

    reading = live_vitals(ps=ps, run=run, cpu_name=lambda: "AMD Ryzen 7 7840HS")

    assert reading == {
        "cpu_percent": 37.2,
        "cpu_logical": 16,
        "cpu_name": "AMD Ryzen 7 7840HS",
        "cpu_physical": 8,
        "cpu_mhz": 3792.0,
        "cpu_max_mhz": 5100.0,
        "ram_used_gb": 10.5,
        "ram_total_gb": 16.0,
        "battery": {"percent": 82, "plugged": True},
        "gpus": [
            {
                "name": "NVIDIA GeForce RTX 4060 Laptop GPU",
                "util_percent": 12.0,
                "vram_used_mb": 1536.0,
                "vram_total_mb": 8188.0,
                "temp_c": 48.0,
                "power_w": 9.87,
            }
        ],
    }
    # CPU load is measured over a real interval, never psutil's instant 0.
    assert ps.intervals == [0.2]
    assert calls[0][0] == "nvidia-smi"


def test_missing_sensors_are_none_not_zero() -> None:
    run, _ = _smi("Quadro P1000, [N/A], 100, 4096, [Not Supported], [N/A]\n")

    (gpu,) = gpu_vitals(run)

    assert gpu["util_percent"] is None
    assert gpu["temp_c"] is None
    assert gpu["power_w"] is None
    assert gpu["vram_total_mb"] == 4096.0


def test_no_nvidia_means_no_gpu_rows_and_desktop_means_no_battery() -> None:
    run, _ = _smi("")
    assert live_vitals(ps=_Psutil(battery=None), run=run, cpu_name=lambda: None)["gpus"] == []
    assert live_vitals(ps=_Psutil(battery=None), run=run, cpu_name=lambda: None)["battery"] is None
    run, _ = _smi("NVIDIA-SMI has failed because it couldn't communicate with the driver.\n")
    assert gpu_vitals(run) == []


def test_route_adds_the_power_mode_off_windows_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(detection, "live_vitals", lambda: {"cpu_percent": 5.0, "gpus": []})
    app = FastAPI()
    app.include_router(deck_routes.router)

    body = TestClient(app).get("/api/deck/vitals").json()

    assert body == {"cpu_percent": 5.0, "gpus": [], "power_mode": None}


@pytest.mark.parametrize(
    "freq",
    [None, SimpleNamespace(current=0.0, min=0.0, max=0.0), SimpleNamespace(current=None, max=None)],
)
def test_unknown_clock_and_cores_are_none_not_zero(freq: object | None) -> None:
    run, _ = _smi("")
    reading = live_vitals(ps=_Psutil(freq=freq, physical=None), run=run, cpu_name=lambda: None)

    assert reading["cpu_mhz"] is None
    assert reading["cpu_max_mhz"] is None
    assert reading["cpu_physical"] is None
    assert reading["cpu_name"] is None


def test_cpu_freq_that_raises_reads_as_unknown() -> None:
    class _Broken(_Psutil):
        def cpu_freq(self) -> object | None:
            raise OSError("no cpufreq on this VM")

    run, _ = _smi("")
    reading = live_vitals(ps=_Broken(), run=run, cpu_name=lambda: None)
    assert (reading["cpu_mhz"], reading["cpu_max_mhz"]) == (None, None)


def test_cpu_name_from_proc_cpuinfo_collapses_whitespace(tmp_path) -> None:
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text(
        "processor\t: 0\nvendor_id\t: GenuineIntel\n"
        "model name\t:   Intel(R) Core(TM)  i7-12700H   CPU @ 2.30GHz  \n"
        "processor\t: 1\nmodel name\t: ignored second core\n",
        encoding="utf-8",
    )
    assert detection._linux_cpu_name(cpuinfo) == "Intel(R) Core(TM) i7-12700H CPU @ 2.30GHz"
    assert detection._linux_cpu_name(tmp_path / "missing") is None


def test_cpu_name_picks_the_source_for_the_platform() -> None:
    def name(system: str, *, win: str | None = None, lin: str | None = None, proc: str = ""):
        return detection.cpu_marketing_name(
            system, windows=lambda: win, linux=lambda: lin, processor=lambda: proc
        )

    assert name("win32", win="AMD Ryzen 9 7945HX", lin="wrong") == "AMD Ryzen 9 7945HX"
    assert name("linux", win="wrong", lin="AMD EPYC 7763") == "AMD EPYC 7763"
    # A miss on the platform source falls back to platform.processor() ...
    assert name("win32", proc="  Intel64 Family 6  ") == "Intel64 Family 6"
    assert name("darwin", win="wrong", lin="wrong", proc="arm") == "arm"
    # ... and an empty one is None, not "unknown".
    assert name("darwin", proc="   ") is None
