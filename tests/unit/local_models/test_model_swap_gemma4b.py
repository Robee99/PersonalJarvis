"""Gemma 4B startup and context settings for the local model swapper."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parents[3] / "scripts" / "windows" / "model_swap.py"


def _load(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    aiohttp = ModuleType("aiohttp")
    aiohttp.web = ModuleType("aiohttp.web")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aiohttp", aiohttp)
    spec = importlib.util.spec_from_file_location("jarvis_model_swap_gemma4b_under_test", SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Import must not read the developer's live C:\models\swap-settings.json.
    with monkeypatch.context() as paths:
        paths.setattr(Path, "is_file", lambda _path: False)
        spec.loader.exec_module(module)
    return module


def test_gemma4b_context_uses_automatic_gpu_layer_fit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    swap = _load(monkeypatch)
    runtime = tmp_path / "llama-server.exe"
    model = tmp_path / "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
    mmproj = tmp_path / "mmproj-F16.gguf"
    for path in (runtime, model, mmproj):
        path.touch()
    monkeypatch.setattr(swap, "RUNTIME", runtime)
    monkeypatch.setattr(swap, "GEMMA_4B_DIR", tmp_path)
    captured: dict[str, Any] = {}

    def fake_popen(args: list[str], **kwargs: Any) -> object:
        captured["args"] = args
        return object()

    monkeypatch.setattr(swap.subprocess, "Popen", fake_popen)

    swap.Swapper().start_gemma_4b()

    args = captured["args"]
    assert args[args.index("-ngl") + 1] == "auto"
    assert args[args.index("-c") + 1] == str(swap.CTX[swap.GEMMA_4B])


def test_gemma4b_startup_reports_a_missing_vision_projector(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    swap = _load(monkeypatch)
    runtime = tmp_path / "llama-server.exe"
    model = tmp_path / "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
    runtime.touch()
    model.touch()
    monkeypatch.setattr(swap, "RUNTIME", runtime)
    monkeypatch.setattr(swap, "GEMMA_4B_DIR", tmp_path)
    monkeypatch.setattr(
        swap.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("must not launch")
    )

    with pytest.raises(FileNotFoundError, match="mmproj-F16.gguf"):
        swap.Swapper().start_gemma_4b()


def test_gemma4b_settings_discard_unsupported_256k_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    swap = _load(monkeypatch)
    settings = tmp_path / "swap-settings.json"
    settings.write_text('{"ctx":{"gemma4b":256000}}', encoding="utf-8")
    monkeypatch.setattr(swap, "SETTINGS", settings)
    swap.CTX[swap.GEMMA_4B] = 131072

    swap._load_settings()

    assert swap.CTX[swap.GEMMA_4B] == 131072
