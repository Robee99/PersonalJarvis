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
    def __init__(self, battery: object | None = None) -> None:
        self.battery = battery
        self.intervals: list[float | None] = []

    def cpu_percent(self, interval: float | None = None) -> float:
        self.intervals.append(interval)
        return 37.25

    def cpu_count(self, logical: bool = True) -> int:
        return 16

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
    ps = _Psutil(battery=SimpleNamespace(percent=81.6, power_plugged=True))

    reading = live_vitals(ps=ps, run=run)

    assert reading == {
        "cpu_percent": 37.2,
        "cpu_logical": 16,
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
    assert live_vitals(ps=_Psutil(battery=None), run=run)["gpus"] == []
    assert live_vitals(ps=_Psutil(battery=None), run=run)["battery"] is None
    run, _ = _smi("NVIDIA-SMI has failed because it couldn't communicate with the driver.\n")
    assert gpu_vitals(run) == []


def test_route_adds_the_power_mode_off_windows_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(detection, "live_vitals", lambda: {"cpu_percent": 5.0, "gpus": []})
    app = FastAPI()
    app.include_router(deck_routes.router)

    body = TestClient(app).get("/api/deck/vitals").json()

    assert body == {"cpu_percent": 5.0, "gpus": [], "power_mode": None}
