"""The Windows installer must ship the optional OCR engine it advertises."""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[3]


def test_full_profile_and_windows_installer_include_rapidocr() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = project["project"]["optional-dependencies"]

    ocr_requirements = [Requirement(item) for item in extras["ocr"]]
    assert any(item.name == "rapidocr" for item in ocr_requirements)

    full_requirements = [Requirement(item) for item in extras["full"]]
    assert any(
        item.name == "personal-jarvis" and "ocr" in item.extras
        for item in full_requirements
    )

    import yaml

    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "desktop-installers.yml").read_text(
            encoding="utf-8"
        )
    )
    windows_steps = workflow["jobs"]["windows"]["steps"]
    install_step = next(
        step
        for step in windows_steps
        if "python scripts/pip_install.py -e" in step.get("run", "")
    )
    assert ".[desktop,dev,ocr]" in install_step["run"]
