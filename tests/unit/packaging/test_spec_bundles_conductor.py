"""The frozen build must carry conductor's non-Python files.

conductor is a top-level package beside ``jarvis``. PyInstaller bundles its
modules but not ``core/schema.sql``, so a native install logged
``FileNotFoundError: ...\\_internal\\conductor\\core\\schema.sql`` at every boot
and the scheduler never started.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC = REPO_ROOT / "jarvis.spec"


def test_the_runtime_schema_exists_where_the_store_reads_it() -> None:
    from conductor.core.store import SCHEMA_FILE

    assert SCHEMA_FILE.is_file()
    assert SCHEMA_FILE.is_relative_to(REPO_ROOT / "conductor")


def test_the_spec_collects_package_data_from_conductor() -> None:
    source = SPEC.read_text(encoding="utf-8")
    assert 'PROJECT_ROOT / "conductor"' in source
