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


def test_stray_cleanup_avoids_slow_listener_enumeration(monkeypatch: pytest.MonkeyPatch) -> None:
    swap = _load(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_ps(command: str, timeout: float) -> str:
        captured["command"] = command
        captured["timeout"] = timeout
        return ""

    monkeypatch.setattr(swap, "_ps", fake_ps)
    swap.close_strays()

    assert "11440" in captured["command"]
    assert "Get-CimInstance Win32_Process" in captured["command"]
    assert "Get-NetTCPConnection" not in captured["command"]
    assert captured["timeout"] == 20


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
        ("stop", swap.UI_VENUS),
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


@pytest.mark.parametrize(
    "alias,context,other,expected",
    [
        ("qwen3-vl-8b", 65536, False, None),
        ("different-model", 65536, False, "identity or context"),
        ("qwen3-vl-8b", 32768, False, "identity or context"),
        ("qwen3-vl-8b", 65536, True, "multiple model backends"),
    ],
)
def test_adopts_only_matching_exclusive_loaded_backend(
    monkeypatch: pytest.MonkeyPatch,
    alias: str,
    context: int,
    other: bool,
    expected: str | None,
) -> None:
    swap = _load(monkeypatch)
    monkeypatch.setattr(swap.aiohttp, "ClientTimeout", lambda **_kwargs: None, raising=False)
    instance = swap.Swapper()

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def json(self):
            return {"model_alias": alias, "default_generation_settings": {"n_ctx": context}}

    class Session:
        def get(self, url, **_kwargs):
            assert url == "http://127.0.0.1:11449/props"
            return Response()

    async def healthy(name):
        return name == swap.QWEN_VL or (other and name == swap.GEMMA)

    instance.session = Session()
    monkeypatch.setattr(instance, "healthy", healthy)
    monkeypatch.setattr(
        swap, "available_mib", lambda: pytest.fail("loaded model needs no new allocation")
    )
    monkeypatch.setattr(
        instance, "start_profile", lambda _name: pytest.fail("must not duplicate model")
    )
    monkeypatch.setattr(
        instance, "stop_profile", lambda _name: pytest.fail("must not stop loaded model")
    )
    if expected:
        with pytest.raises(RuntimeError, match=expected):
            asyncio.run(instance.ensure(swap.QWEN_VL))
        assert instance.active is None
    else:
        asyncio.run(instance.ensure(swap.QWEN_VL))
        assert instance.active == swap.QWEN_VL
        assert instance.last_used[swap.QWEN_VL] > 0


@pytest.mark.parametrize("error", [RuntimeError("wrong identity"), TimeoutError("props timeout")])
def test_startup_adoption_failure_keeps_recovery_available(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    swap = _load(monkeypatch)
    instance = swap.Swapper()

    async def refused(_name):
        raise error

    monkeypatch.setattr(instance, "adopt_running", refused)
    assert asyncio.run(instance.restore_main_backend()) is False
    assert instance.active is None
    assert "recovery endpoints remain available" in caplog.text


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
    monkeypatch.setattr(swap, "QWEN_VL_RUNTIME", runtime)
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

    # CREATE_NO_WINDOW only exists on Windows; the flag value is what matters.
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    instance = swap.Swapper()
    instance.start_qwen_vl()
    args = captured["args"]

    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("--port") + 1] == "11449"
    assert args[args.index("-c") + 1] == "65536"
    assert args[args.index("-ngl") + 1] == "99"
    # turbo3 K returned empty output past ~10k tokens; turbo4 K is the tested one.
    assert args[args.index("-ctk") + 1] == "turbo4"
    assert "--no-mmproj-offload" in args and "--no-mmap" in args
    assert "--mmproj" in args and "--offline" in args and "--jinja" in args
    assert captured["kwargs"]["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_qwen_vl_missing_model_files_fails_before_launch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    swap = _load(monkeypatch)
    monkeypatch.setattr(swap, "QWEN_VL_RUNTIME", tmp_path / "missing-runtime.exe")
    monkeypatch.setattr(swap, "QWEN_VL_SNAP", tmp_path / "missing-snapshot")
    monkeypatch.setattr(
        subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("must not launch")
    )

    with pytest.raises(FileNotFoundError, match="startup files are missing"):
        swap.Swapper().start_qwen_vl()


def test_ui_venus_launch_uses_the_grounding_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    swap = _load(monkeypatch)
    runtime = tmp_path / "llama-server.exe"
    runtime.touch()
    (tmp_path / "UI-Venus-2-9B-Q4_K_L.gguf").touch()
    (tmp_path / "mmproj-UI-Venus-2-9B-f16.gguf").touch()
    monkeypatch.setattr(swap, "RUNTIME", runtime)
    monkeypatch.setattr(swap, "UI_VENUS_DIR", tmp_path)
    captured: dict[str, Any] = {}

    class FakeProcess:
        def poll(self) -> int:
            return 0

    def fake_popen(args: list[str], **kwargs: Any) -> FakeProcess:
        captured["args"] = args
        return FakeProcess()

    # CREATE_NO_WINDOW only exists on Windows; the flag value is what matters.
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    swap.Swapper().start_ui_venus()
    args = captured["args"]

    assert swap.FRONT[swap.UI_VENUS] == 11441
    assert args[args.index("--port") + 1] == "11451"
    assert args[args.index("--alias") + 1] == "ui-venus-2-9b"
    assert args[args.index("--host") + 1] == "127.0.0.1"
    # cuda-b11388 rejects --no-mmap; -lm none is its spelling.
    assert "--no-mmap" not in args
    assert args[args.index("-lm") + 1] == "none"
    assert "--offline" in args


def test_context_meter_reads_the_final_stream_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    swap = _load(monkeypatch)
    tail = (
        b'data: {"choices":[{"delta":{}}],"usage":{"completion_tokens":12,"prompt_tokens":4100,'
        b'"total_tokens":4112},"timings":{"cache_n":4000,"prompt_n":100,"prompt_ms":90.1,'
        b'"predicted_n":12,"predicted_ms":400.0}}\n\ndata: [DONE]\n\n'
    )
    assert swap.context_used(tail) == 4112
    # Without timings the OpenAI usage block answers.
    assert swap.context_used(b'"usage":{"prompt_tokens":900,"completion_tokens":30}') == 930
    assert swap.context_used(b"data: [DONE]") is None
    assert swap.CTX[swap.QWEN_VL] == 65536
