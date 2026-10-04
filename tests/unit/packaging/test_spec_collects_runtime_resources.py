"""The frozen build carries every file the app reads from disk at runtime.

PyInstaller follows Python imports only. A file read through
``Path(__file__).parent / ...``, ``repo_root()`` or a native ``dlopen`` is
missing from ``_internal`` unless ``jarvis.spec`` collects it, and the app then
fails only on a user's machine — ``...\\_internal\\conductor\\core\\schema.sql``
(the scheduler never started) and ``_internal\\vosk`` (no wake word, and a
PATH-growing retry loop) both shipped that way.

The real ``jarvis.spec`` is executed here against the real checkout with
stand-in PyInstaller names, and what it hands to ``Analysis`` is checked:

- every file the wheel ships as package data is also in the frozen build, at
  the same package-relative place (one list, two packagers, no drift);
- the named runtime reads (conductor schema + seeds, brand marks, onboarding
  terms) are collected;
- vosk's native library is collected when vosk is installed, and a build
  machine without vosk still produces a spec.

What PyInstaller then does with these entries on Windows/macOS is not run here.
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = REPO_ROOT / "jarvis.spec"


def _exec_spec(
    monkeypatch: pytest.MonkeyPatch,
    *,
    dynamic_libs: dict[str, list[tuple[str, str]]] | None = None,
    hide: tuple[str, ...] = (),
) -> dict:
    """Run the real spec with stand-in PyInstaller names; return Analysis kwargs."""
    calls: dict[str, dict] = {}

    def recorder(name: str):
        def _call(*args: object, **kwargs: object) -> types.SimpleNamespace:
            calls[name] = kwargs
            return types.SimpleNamespace(
                pure=[], zipped_data=[], scripts=[], binaries=[], zipfiles=[], datas=[]
            )

        return _call

    libs = dynamic_libs or {}
    hooks = types.ModuleType("PyInstaller.utils.hooks")
    hooks.collect_data_files = lambda *a, **k: []  # type: ignore[attr-defined]
    hooks.collect_submodules = lambda *a, **k: []  # type: ignore[attr-defined]
    hooks.collect_dynamic_libs = lambda pkg, *a, **k: list(libs.get(pkg, []))  # type: ignore[attr-defined]
    hooks.copy_metadata = lambda *a, **k: []  # type: ignore[attr-defined]
    for name, module in (
        ("PyInstaller", types.ModuleType("PyInstaller")),
        ("PyInstaller.utils", types.ModuleType("PyInstaller.utils")),
        ("PyInstaller.utils.hooks", hooks),
        ("webview", types.ModuleType("webview")),
        ("pystray", types.ModuleType("pystray")),
        ("PIL", types.ModuleType("PIL")),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys, "platform", "linux")
    if hide:
        real_find_spec = importlib.util.find_spec

        def _find_spec(name: str, *args: object, **kwargs: object):
            if name in hide:
                return None
            return real_find_spec(name, *args, **kwargs)

        monkeypatch.setattr(importlib.util, "find_spec", _find_spec)

    namespace: dict[str, object] = {
        "SPECPATH": str(REPO_ROOT),
        "__file__": str(SPEC_PATH),
    }
    for builtin in ("Analysis", "PYZ", "EXE", "COLLECT", "BUNDLE"):
        namespace[builtin] = recorder(builtin)
    source = SPEC_PATH.read_text(encoding="utf-8")
    exec(compile(source, str(SPEC_PATH), "exec"), namespace)  # noqa: S102
    return calls["Analysis"]


@pytest.fixture(scope="module")
def analysis() -> dict:
    with pytest.MonkeyPatch.context() as mp:
        return _exec_spec(mp)


def _collected(analysis: dict) -> set[tuple[Path, str]]:
    out: set[tuple[Path, str]] = set()
    for src, dest in analysis["datas"]:
        path = Path(src).resolve()
        if path.is_dir():  # a directory entry collects its whole tree
            for entry in path.rglob("*"):
                if entry.is_file():
                    rel = entry.relative_to(path).parent
                    out.add((entry, str(Path(dest) / rel).replace("\\", "/")))
        else:
            out.add((path, str(Path(dest)).replace("\\", "/")))
    return out


def _expect(rel: str) -> tuple[Path, str]:
    path = (REPO_ROOT / rel).resolve()
    return path, str(Path(rel).parent).replace("\\", "/")


def _wheel_package_data() -> list[str]:
    """Every file pyproject ships as package data, repo-relative."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    table = data["tool"]["setuptools"]["package-data"]
    files: set[str] = set()
    for package, patterns in table.items():
        root = REPO_ROOT / package.replace(".", "/")
        for pattern in patterns:
            for entry in root.glob(pattern):
                if not entry.is_file() or "__pycache__" in entry.parts:
                    continue
                if entry.suffix in {".pyc", ".pyo"} or "node_modules" in entry.parts:
                    continue
                files.add(entry.relative_to(REPO_ROOT).as_posix())
    return sorted(files)


def test_every_wheel_package_data_file_is_in_the_frozen_build(analysis: dict) -> None:
    collected = _collected(analysis)
    wheel_files = _wheel_package_data()
    assert any(f.startswith("conductor/") for f in wheel_files)
    missing = [rel for rel in wheel_files if _expect(rel) not in collected]
    assert missing == [], f"shipped in the wheel but not frozen: {missing[:20]}"


@pytest.mark.parametrize(
    "rel",
    (
        "conductor/core/schema.sql",
        "conductor/seed/daily_standup.yaml",
        "jarvis/ui/web/frontend/src/assets/providers/LOGOS.md",
        "jarvis/ui/web/frontend/src/assets/providers/claude.svg",
        "jarvis/ui/web/frontend/src/assets/brands/LOGOS.md",
        "docs/legal/TERMS.md",
        "jarvis/skills/catalog/seed_catalog.json",
        "jarvis/core/review/verdict_schema.json",
        "jarvis/skills/safe_imports.txt",
        "jarvis/agent_screen/runner/sandbox_runner.ps1",
        "jarvis/plugins/tool/calendar_bot.mjs",
    ),
)
def test_named_runtime_reads_are_collected(analysis: dict, rel: str) -> None:
    assert (REPO_ROOT / rel).is_file(), f"{rel} moved; update this list"
    assert _expect(rel) in _collected(analysis)


def test_every_sql_schema_is_collected(analysis: dict) -> None:
    collected = _collected(analysis)
    schemas = [
        p.relative_to(REPO_ROOT).as_posix()
        for root in (REPO_ROOT / "jarvis", REPO_ROOT / "conductor")
        for p in root.rglob("*.sql")
        if "node_modules" not in p.parts
    ]
    assert schemas
    assert [s for s in schemas if _expect(s) not in collected] == []


@pytest.mark.skipif(importlib.util.find_spec("vosk") is None, reason="vosk not installed")
def test_vosk_native_library_lands_in_its_package_folder(analysis: dict) -> None:
    spec = importlib.util.find_spec("vosk")
    assert spec is not None and spec.submodule_search_locations
    folder = Path(next(iter(spec.submodule_search_locations)))
    libs = [p for p in folder.iterdir() if p.name.startswith("libvosk")]
    assert libs, "the installed vosk wheel carries no native library"
    binaries = {(Path(src).resolve(), dest) for src, dest in analysis["binaries"]}
    for lib in libs:
        assert (lib.resolve(), "vosk") in binaries
    assert "vosk" in analysis["hiddenimports"]


def test_dynamic_libs_from_the_hook_pass_through_without_duplicates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Windows layout: libvosk.dll plus its MinGW runtime DLLs, once each."""
    if importlib.util.find_spec("vosk") is None:
        pytest.skip("vosk not installed")
    dlls = [(str(tmp_path / name), "vosk") for name in ("libvosk.dll", "libstdc++-6.dll")]
    result = _exec_spec(monkeypatch, dynamic_libs={"vosk": dlls})
    for entry in dlls:
        assert result["binaries"].count(entry) == 1


def test_a_build_machine_without_vosk_still_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _exec_spec(monkeypatch, hide=("vosk", "_cffi_backend"))
    assert not [b for b in result["binaries"] if b[1] == "vosk"]
    assert "vosk" not in result["hiddenimports"]
