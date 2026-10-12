"""The frozen build must carry every module an entry point names.

PyInstaller follows static imports only. The MCP, CLI and Marketplace tool
loaders are reached only through ``jarvis.tool`` entry points, so the installed
app logged "46 MCP tools registered" while the brain's tool list had none
(2026-10-12).
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC = REPO_ROOT / "jarvis.spec"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _spec_function(name: str):
    tree = ast.parse(SPEC.read_text(encoding="utf-8"))
    node = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name
    )
    namespace: dict = {"Path": Path}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SPEC), "exec"), namespace)  # noqa: S102
    return namespace[name]


def test_every_entry_point_module_becomes_a_hidden_import() -> None:
    modules = _spec_function("_entry_point_modules")(PYPROJECT)

    groups = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["entry-points"]
    named = {t.split(":", 1)[0] for g in groups.values() for t in g.values()}
    assert set(modules) == named
    for loader in ("jarvis.mcp.loader", "jarvis.clis.loader", "jarvis.marketplace.plugin_loader"):
        assert loader in modules


def test_the_spec_adds_them() -> None:
    source = SPEC.read_text(encoding="utf-8")
    assert 'hiddenimports += _entry_point_modules(PROJECT_ROOT / "pyproject.toml")' in source
