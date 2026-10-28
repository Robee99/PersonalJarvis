"""An upgrade install removes the package metadata an older build left behind.

Live 2026-10-25 the installed app still carried personal_jarvis-2.7.1.dist-info
from a 17 October install beside the new build's metadata. importlib.metadata
read the old entry points, so system_volume, window_control and every tool
added since were never registered, and Gemma turned the assistant's own voice
down when asked for the computer's volume.
"""

from __future__ import annotations

import re
from pathlib import Path

ISS = Path(__file__).resolve().parents[3] / "packaging" / "windows" / "PersonalJarvis.iss"


def _section(text: str, name: str) -> str:
    m = re.search(rf"^\[{name}\]\s*$(.*?)(?=^\[|\Z)", text, re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""


def test_old_dist_info_and_egg_info_are_deleted_before_the_new_files_land() -> None:
    text = ISS.read_text(encoding="utf-8")
    deletes = _section(text, "InstallDelete")

    assert r'"{app}\_internal\personal_jarvis-*.dist-info"' in deletes
    assert r'"{app}\_internal\personal_jarvis.egg-info"' in deletes
    # Inno runs [InstallDelete] before [Files]; the section order documents it.
    assert text.index("[InstallDelete]") < text.index("[Files]")
