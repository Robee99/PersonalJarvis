# ADR-0041 — RapidOCR is the first local OCR engine; Tesseract stays as fallback

**Status:** Accepted (2026-10-03)
**Date:** 2026-10-03
**Reference:** [Final hardening evidence](../research/final-hardening-evidence.md) F9–F13, §9.3

## Context

Screen Context runs OCR when a window has too little accessibility text. OCR
only worked through `pytesseract`, which needs a native Tesseract install. That
install is not on PyPI and is not on the owner's PC. Masking uncertain words
and burning secrets out of pixels both need per-word boxes and confidence.

The candidates compared:
- RapidOCR: Apache-2.0 code and weights. The PP-OCRv6 models are bundled in
  the wheel and run on the onnxruntime PJ already ships. It gives per-word
  boxes and scores.
- Windows.Media.Ocr: no confidence at any level.
- PaddleOCR: needs the paddlepaddle framework.
- OmniParser: mixed CC-BY/AGPL licences and a heavy dependency set.

## Decision

1. `uitext` tries RapidOCR first, then Tesseract. Both feed one word and line
   model, so masking and redaction are the same for either engine. RapidOCR
   scores (0–1) are scaled to the existing 0–100 threshold.
2. RapidOCR's own line filter is turned off (`text_score=0`), so an uncertain
   line still reaches the redaction geometry.
3. Only the bundled models are used, so nothing is downloaded at run time.
4. The install stays optional and is probed like pytesseract.
   `ocr_engine_status()` is the single availability check, used by the
   Settings route and the accuracy gate.

## Consequences

- On the labelled fixtures in `test_ocr_accuracy_gate.py`, RapidOCR measured
  0.981 normalised character accuracy (target 0.95). That run was in a Linux
  container, on synthetic text.
- Per-image CPU time in that container was about 2.1 s. Device latency is not
  measured.
- `[screen_context].ocr_enabled` stays off by default. Turning it on and
  installing `rapidocr` are owner decisions.
