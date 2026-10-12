"""RapidOCR is optional: absent or broken, OCR falls back or says it is unavailable.

ADR-0041 makes RapidOCR the first engine and Tesseract the fallback. The
frozen app showed why "importable" is not "working": a build can carry the
package's modules without its config and ONNX models. Pinned here:

- an installed-but-broken RapidOCR falls back to Tesseract, logs ONE line and
  is not rebuilt on every capture;
- with no engine at all, every capture is a typed OCR_UNAVAILABLE and the log
  carries one line, not one per capture;
- the status probe (Settings) does not call a model-less RapidOCR ready;
- the smoke script passes with a real engine and skips cleanly without one.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from types import SimpleNamespace

import pytest

from jarvis.screen_context import uitext
from jarvis.screen_context.models import DegradationCode
from scripts.ci import check_ocr_smoke as smoke


@pytest.fixture(autouse=True)
def _fresh_engine_state(monkeypatch) -> None:
    monkeypatch.setattr(uitext, "_RAPIDOCR_ENGINE", None)
    monkeypatch.setattr(uitext, "_RAPIDOCR_FAILURE", None)
    monkeypatch.setattr(uitext, "_NO_ENGINE_LOGGED", False)


def _tesseract(monkeypatch) -> None:
    data = {
        "text": ["Hello"], "left": [5], "top": [5], "width": [40], "height": [10],
        "conf": [95], "block_num": [1], "par_num": [1], "line_num": [1],
    }
    fake = SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_data=lambda *_a, **_k: data,
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake)


def test_broken_rapidocr_falls_back_to_tesseract_and_logs_once(monkeypatch, caplog) -> None:
    builds: list[int] = []

    class _Broken:
        def __init__(self, params=None) -> None:
            builds.append(1)
            raise FileNotFoundError("rapidocr/config.yaml")

    monkeypatch.setitem(sys.modules, "rapidocr", SimpleNamespace(RapidOCR=_Broken))
    _tesseract(monkeypatch)
    caplog.set_level(logging.DEBUG, logger="jarvis.screen_context.uitext")

    results = [uitext.ocr_supplement_with_regions(object()) for _ in range(5)]

    assert [r.text for r in results] == ["Hello"] * 5
    assert all(r.degradation is None for r in results)
    assert builds == [1]  # not rebuilt per capture
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1 and "Tesseract" in warnings[0].getMessage()
    ready, reason = uitext._rapidocr_problem() is None, uitext._rapidocr_problem()  # noqa: SLF001
    assert ready is False and "could not start" in reason


def test_no_engine_is_unavailable_with_one_log_line(monkeypatch, caplog) -> None:
    monkeypatch.setitem(sys.modules, "rapidocr", None)
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    caplog.set_level(logging.DEBUG, logger="jarvis.screen_context.uitext")

    results = [uitext.ocr_supplement_with_regions(object()) for _ in range(10)]

    assert all(r.degradation.code is DegradationCode.OCR_UNAVAILABLE for r in results)
    lines = [r for r in caplog.records if r.levelno >= logging.INFO]
    assert len(lines) == 1


def test_status_does_not_call_a_model_less_rapidocr_ready(monkeypatch, tmp_path) -> None:
    package = tmp_path / "rapidocr"
    package.mkdir()  # modules only: no config.yaml, no models/*.onnx
    real_find_spec = importlib.util.find_spec

    def _find_spec(name: str, *args, **kwargs):
        if name == "rapidocr":
            return SimpleNamespace(submodule_search_locations=[str(package)])
        if name == "pytesseract":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", _find_spec)
    if real_find_spec("onnxruntime") is None:
        pytest.skip("onnxruntime absent: the probe stops before the model check")

    ready, reason = uitext.ocr_engine_status()
    assert ready is False
    assert "without its bundled models" in reason

    (package / "config.yaml").write_text("Global: {}\n", encoding="utf-8")
    (package / "models").mkdir()
    (package / "models" / "det.onnx").write_bytes(b"onnx")
    assert uitext.ocr_engine_status() == (True, "")


def test_status_without_onnxruntime_is_honest(monkeypatch) -> None:
    def _find_spec(name: str, *args, **kwargs):
        if name == "rapidocr":
            return SimpleNamespace(submodule_search_locations=[])
        return None

    monkeypatch.setattr(importlib.util, "find_spec", _find_spec)
    ready, reason = uitext.ocr_engine_status()
    assert ready is False
    assert "onnxruntime" in reason


def test_smoke_skips_cleanly_without_an_engine(monkeypatch) -> None:
    pytest.importorskip("PIL")
    monkeypatch.setitem(sys.modules, "rapidocr", None)
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name, *a, **k: None)
    result = smoke.run_adapter_smoke()
    assert result.status == "skip", result


@pytest.mark.skipif(
    importlib.util.find_spec("rapidocr") is None or importlib.util.find_spec("PIL") is None,
    reason="RapidOCR (the [ocr] extra) or Pillow is not installed",
)
def test_smoke_reads_a_generated_image_with_rapidocr() -> None:
    result = smoke.run_adapter_smoke()
    assert result.status == "pass", result.detail


def test_bundle_check_flags_missing_runtime_files(tmp_path) -> None:
    internal = tmp_path / "Jarvis" / "_internal"
    (internal / "rapidocr" / "models").mkdir(parents=True)
    (internal / "vosk").mkdir()
    problems = smoke.check_bundle(tmp_path / "Jarvis")
    assert any("config.yaml" in p for p in problems)
    assert any("ONNX" in p for p in problems)
    assert any("libvosk" in p for p in problems)

    (internal / "rapidocr" / "config.yaml").write_text("x", encoding="utf-8")
    (internal / "rapidocr" / "default_models.yaml").write_text("x", encoding="utf-8")
    (internal / "rapidocr" / "models" / "det.onnx").write_bytes(b"x")
    (internal / "vosk" / "libvosk.dll").write_bytes(b"MZ")
    assert smoke.check_bundle(tmp_path / "Jarvis") == []
