"""The Qwen3-VL model-swap profile stays local and exclusive with other models."""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parents[3] / "scripts" / "windows" / "model_swap.py"


def _load(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    # The production script runs in Hermes's venv, which has aiohttp. These
    # focused unit tests exercise only configuration and process lifecycle,
    # so inject the optional runtime dependency instead of installing it into
    # the JARVIS test environment.
    aiohttp = ModuleType("aiohttp")
    aiohttp.web = ModuleType("aiohttp.web")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aiohttp", aiohttp)
    spec = importlib.util.spec_from_file_location("jarvis_model_swap_under_test", SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qwen_vl_front_and_backend_are_loopback_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    swap = _load(monkeypatch)
    assert swap.FRONT[swap.QWEN_VL] == 11440
    assert swap.BACK[swap.QWEN_VL] == 11449
    assert swap.MODEL_ID[swap.QWEN_VL] == "qwen3-vl-8b"
    assert len(set(swap.FRONT.values())) == len(swap.FRONT)
    assert len(set(swap.BACK.values())) == len(swap.BACK)


def test_qwen_vl_transition_stops_other_profiles_before_starting_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    swap = _load(monkeypatch)
    instance = swap.Swapper()
    instance.active = swap.GEMMA
    events: list[tuple[str, str]] = []

    async def unhealthy(_name: str) -> bool:
        return False

    async def ready(_name: str) -> None:
        return None

    monkeypatch.setattr(swap, "available_mib", lambda: 8192)
    monkeypatch.setattr(swap, "_working_set_mib", lambda _port: 0)
    monkeypatch.setattr(instance, "healthy", unhealthy)
    monkeypatch.setattr(instance, "wait_ready", ready)
    monkeypatch.setattr(instance, "stop_profile", lambda name: events.append(("stop", name)))
    monkeypatch.setattr(instance, "start_profile", lambda name: events.append(("start", name)))

    asyncio.run(instance.ensure(swap.QWEN_VL))

    assert events == [
        ("stop", swap.GEMMA),
        ("stop", swap.QWEN),
        ("start", swap.QWEN_VL),
    ]
    assert instance.active == swap.QWEN_VL


def test_qwen_vl_memory_guard_refuses_without_stopping_active_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    swap = _load(monkeypatch)
    instance = swap.Swapper()
    instance.active = swap.GEMMA
    events: list[str] = []

    async def unhealthy(_name: str) -> bool:
        return False

    monkeypatch.setattr(swap, "available_mib", lambda: swap.QWEN_VL_RAM_NEEDED_MIB - 1)
    monkeypatch.setattr(swap, "_working_set_mib", lambda _port: 0)
    monkeypatch.setattr(instance, "healthy", unhealthy)
    monkeypatch.setattr(instance, "stop_profile", lambda name: events.append("stop:" + name))

    with pytest.raises(MemoryError, match="Qwen3-VL needs"):
        asyncio.run(instance.ensure(swap.QWEN_VL))

    assert events == []
    assert instance.active == swap.GEMMA


def test_qwen_vl_launch_is_validated_and_uses_optimized_loopback_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    swap = _load(monkeypatch)
    runtime = tmp_path / "llama-server.exe"
    model = tmp_path / "qwen-vl.gguf"
    mmproj = tmp_path / "mmproj.gguf"
    for file in (runtime, model, mmproj):
        file.touch()
    monkeypatch.setattr(swap, "RUNTIME", runtime)
    monkeypatch.setattr(swap, "QWEN_VL_SNAP", tmp_path)
    model.rename(tmp_path / "Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf")
    mmproj.rename(tmp_path / "mmproj-F16.gguf")
    captured: dict[str, Any] = {}

    class FakeProcess:
        def poll(self) -> int:
            return 0

    def fake_popen(args: list[str], **kwargs: Any) -> FakeProcess:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    instance = swap.Swapper()
    instance.start_qwen_vl()
    args = captured["args"]

    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("--port") + 1] == "11449"
    assert args[args.index("-c") + 1] == "8192"
    assert args[args.index("--fit-target") + 1] == "2048"
    assert args[args.index("-ctk") + 1] == "q4_0"
    assert "--mmproj" in args and "--offline" in args and "--jinja" in args
    assert captured["kwargs"]["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_qwen_vl_missing_model_files_fails_before_launch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    swap = _load(monkeypatch)
    monkeypatch.setattr(swap, "RUNTIME", tmp_path / "missing-runtime.exe")
    monkeypatch.setattr(swap, "QWEN_VL_SNAP", tmp_path / "missing-snapshot")
    monkeypatch.setattr(
        subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("must not launch")
    )

    with pytest.raises(FileNotFoundError, match="startup files are missing"):
        swap.Swapper().start_qwen_vl()
