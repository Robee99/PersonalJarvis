"""The browser worker's sibling modules ship next to it in the frozen build.

``live_runner.py`` runs in its own venv as a plain script, so it imports its
helpers (``from page_cursor import ...``) from the folder it sits in, and
PyInstaller never sees those imports. Live 2026-10-25 the installed build
answered a form-filling request with "No module named 'page_cursor'" because
only the other helpers were listed in ``jarvis.spec``.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BROWSER = REPO_ROOT / "jarvis" / "society" / "browser"


def test_every_helper_the_worker_imports_is_bundled_beside_it() -> None:
    runner = (BROWSER / "live_runner.py").read_text(encoding="utf-8")
    imported = set(re.findall(r"^\s*from (\w+) import", runner, re.MULTILINE))
    helpers = {name for name in imported if (BROWSER / f"{name}.py").exists()}
    spec = (REPO_ROOT / "jarvis.spec").read_text(encoding="utf-8")

    assert "page_cursor" in helpers
    missing = sorted(name for name in helpers if f'"jarvis/society/browser/{name}.py"' not in spec)
    assert missing == []
