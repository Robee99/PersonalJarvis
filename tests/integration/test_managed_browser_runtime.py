"""Opt-in real managed-browser installation and local rendering acceptance."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from jarvis.society.browser import install


def test_managed_browser_renders_and_types_with_real_runtime() -> None:
    configured_root = os.environ.get("JARVIS_BROWSER_ACCEPTANCE_ROOT", "").strip()
    if not configured_root:
        pytest.skip("set JARVIS_BROWSER_ACCEPTANCE_ROOT to an isolated acceptance directory")
    root = Path(configured_root).expanduser().resolve()
    result = install.ensure_installed(root)
    assert result["installed"] is True
    assert result["phase"] == "done" or install.is_installed(root)

    # Exercise the actual isolated runner, not a fake browser. Its probe renders
    # local HTML, fills an input, reads the value back, and checks a screenshot.
    output = install._run(
        [str(install.venv_python(root)), str(install.runner_path()), "--probe"],
        env=install.worker_env(root),
        timeout=120,
    )
    probes = [
        json.loads(line)
        for line in output.splitlines()
        if line.startswith('{"kind": "probe"')
    ]
    assert probes and probes[-1]["ok"] is True
    assert probes[-1]["version"]
    assert Path(probes[-1]["executable"]).resolve().is_relative_to(install.install_root(root))
    assert install.is_installed(root)
