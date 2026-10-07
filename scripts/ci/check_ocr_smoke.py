"""OCR smoke check: the adapter reads a tiny generated image, or skips honestly.

Two checks, both optional-engine aware (RapidOCR is installed separately,
ADR-0041):

* ``run_adapter_smoke()`` imports ``jarvis.screen_context.uitext`` and runs
  ``ocr_supplement_with_regions`` on a small rendered image. With no usable
  engine it must report a typed ``OCR_UNAVAILABLE`` and the run is a SKIP, not
  a failure.
* ``--bundle DIR`` inspects a built PyInstaller bundle (``dist/Jarvis``): when
  it carries RapidOCR or vosk, their runtime files must be next to them
  (RapidOCR's config and ONNX models; vosk's native library). The frozen app
  loads both from their package folders, which import analysis never sees.

Exit status: 0 = passed or skipped (the reason is printed), 1 = failed.

    python scripts/ci/check_ocr_smoke.py
    python scripts/ci/check_ocr_smoke.py --bundle dist/Jarvis
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SMOKE_TEXT = "Invoice 42"


@dataclass(frozen=True)
class SmokeResult:
    status: str  # "pass", "skip" or "fail"
    detail: str


def _render(text: str):
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    font = ImageFont.load_default(size=28)
    left, top, right, bottom = font.getbbox(text)
    image = Image.new("RGB", (right - left + 40, bottom - top + 30), (255, 255, 255))
    ImageDraw.Draw(image).text((20 - left, 15 - top), text, font=font, fill=(0, 0, 0))
    return image


def run_adapter_smoke() -> SmokeResult:
    """Run the OCR adapter once over a rendered image."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from jarvis.screen_context.models import DegradationCode  # noqa: PLC0415
    from jarvis.screen_context.uitext import (  # noqa: PLC0415
        ocr_engine_status,
        ocr_supplement_with_regions,
    )

    ready, reason = ocr_engine_status()
    try:
        image = _render(SMOKE_TEXT)
    except ImportError as exc:
        return SmokeResult("skip", f"Pillow is not installed ({exc})")
    result = ocr_supplement_with_regions(image)
    code = result.degradation.code if result.degradation is not None else None
    if not ready:
        if code is not DegradationCode.OCR_UNAVAILABLE:
            return SmokeResult(
                "fail", f"no engine ({reason}) but OCR did not report OCR_UNAVAILABLE"
            )
        return SmokeResult("skip", f"no usable OCR engine: {reason}")
    if code is DegradationCode.OCR_UNAVAILABLE:
        message = result.degradation.message if result.degradation else ""
        return SmokeResult("fail", f"engine reported ready but OCR was unavailable: {message}")
    heard = " ".join(result.text.split()).casefold()
    if "invoice" not in heard or "42" not in heard:
        return SmokeResult("fail", f"read {result.text!r}, expected {SMOKE_TEXT!r}")
    if not result.regions:
        return SmokeResult("fail", "text was read but no redaction geometry came back")
    return SmokeResult("pass", f"read {result.text!r}")


def _bundle_internal(bundle: Path) -> Path:
    """The folder PyInstaller puts packages in (``_internal`` since 6.0)."""
    for candidate in (
        bundle / "_internal",
        bundle / "Contents" / "Frameworks",  # a macOS .app
        bundle,
    ):
        if candidate.is_dir():
            return candidate
    return bundle


def check_bundle(bundle: Path) -> list[str]:
    """Problems with the runtime files of RapidOCR and vosk inside a bundle."""
    problems: list[str] = []
    internal = _bundle_internal(bundle)
    rapidocr = internal / "rapidocr"
    if rapidocr.is_dir():
        for name in ("config.yaml", "default_models.yaml"):
            if not (rapidocr / name).is_file():
                problems.append(f"{rapidocr / name} is missing")
        if not any((rapidocr / "models").glob("*.onnx")):
            problems.append(f"{rapidocr / 'models'} has no ONNX model")
    vosk = internal / "vosk"
    if vosk.is_dir() and not any(vosk.glob("libvosk*")):
        problems.append(f"{vosk} has no native libvosk library")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundle", type=Path, help="a built PyInstaller bundle (dist/Jarvis)")
    args = parser.parse_args(argv)

    failed = False
    if args.bundle is not None:
        if not args.bundle.is_dir():
            print(f"FAIL bundle: {args.bundle} is not a directory")
            return 1
        problems = check_bundle(args.bundle)
        for problem in problems:
            print(f"FAIL bundle: {problem}")
        if not problems:
            print(f"PASS bundle: {args.bundle}")
        failed = bool(problems)

    result = run_adapter_smoke()
    print(f"{result.status.upper()} ocr: {result.detail}")
    failed = failed or result.status == "fail"
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
