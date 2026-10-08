"""Provision the shipped native Hermes control plugin through its own loader."""

from __future__ import annotations

import os
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

PLUGIN_NAME = "jarvis-control"
_FILES = ("__init__.py", "plugin.yaml")
_MARKER = ".jarvis-managed"


def install_controls(
    *, home: Path, argv: list[str] | None, run: Callable[[list[str]], tuple[int, str]]
) -> tuple[str, str]:
    if not argv:
        return "failed", "the hermes command is not on PATH"
    target = home / "plugins" / PLUGIN_NAME
    if target.exists() and not (target / _MARKER).is_file():
        return "failed", "an unmanaged jarvis-control plugin already exists; it was preserved"
    target.mkdir(parents=True, exist_ok=True)
    source = files("jarvis").joinpath("assets", "hermes-control")
    changed = False
    for name in _FILES:
        content = source.joinpath(name).read_bytes()
        path = target / name
        if path.is_file() and path.read_bytes() == content:
            continue
        temporary = target / (name + ".jarvis-tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)
        changed = True
    (target / _MARKER).write_text("Managed by PersonalJarvis free-voice setup.\n", encoding="utf-8")
    code, _output = run([*argv, "plugins", "enable", PLUGIN_NAME])
    if code != 0:
        return "failed", "Hermes could not enable its Jarvis control plugin"
    return "changed" if changed else "ok", (
        "Native conversation controls installed; restart Hermes to load them"
    )


def controls_for() -> Callable[[], tuple[str, str]]:
    def install() -> tuple[str, str]:
        from jarvis.cli_ctl.free_voice import _local_hermes_argv, _run
        from jarvis.plugins.brain.hermes import _hermes_home_candidates

        homes = _hermes_home_candidates()
        home = next((h for h in homes if (h / ".env").is_file()), homes[0])
        return install_controls(home=home, argv=_local_hermes_argv(), run=_run)

    return install
