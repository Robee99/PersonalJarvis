"""Catch a stale shipped UI even when source-only component tests pass."""

from __future__ import annotations

from pathlib import Path

from scripts.ci.check_dist_consistency import _refs_in

ROOT = Path(__file__).resolve().parents[3]
DIST = ROOT / "jarvis/ui/web/dist"
BRANDS = ROOT / "jarvis/ui/web/frontend/src/assets/brands"


def _reachable_javascript() -> str:
    pending = [DIST / "index.html"]
    visited: set[Path] = set()
    javascript: list[str] = []
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        assert path.is_file(), f"The delivered frontend references a missing asset: {path.name}"
        if path.suffix not in {".html", ".js", ".mjs", ".css"}:
            continue
        content = path.read_text(encoding="utf-8")
        if path.suffix == ".js":
            javascript.append(content)
        pending.extend(DIST / "assets" / name for name in _refs_in(content))
    return "\n".join(javascript)


def test_delivered_frontend_contains_the_chat_tool_picker_and_all_local_logos():
    javascript = _reachable_javascript()
    assert "tool-picker-row" in javascript, (
        "The shipped bundle does not contain the current chat tool picker. Build the current checkout."
    )
    missing = [
        path.name
        for path in BRANDS.iterdir()
        if path.suffix in {".svg", ".png", ".ico"}
        and f"../assets/brands/{path.name}" not in javascript
    ]
    assert not missing, (
        f"Local originals missing from the delivered logo map: {missing}. "
        "Source files and passing source tests do not prove that users received them."
    )
