"""Importing a notes folder or Obsidian vault into the wiki (``importer``).

Real files in ``tmp_path`` throughout: a source folder, a vault, and the FTS
database the wiki searches. The last test drives the HTTP routes the Memory
Orb's Import button calls.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.memory.wiki import importer
from jarvis.memory.wiki.importer import (
    IMPORT_DIR,
    ImportJobs,
    ImportRefused,
    destination_for,
    run_import,
    source_label,
)
from jarvis.memory.wiki.search import VaultSearch

MARKER = "JARVIS_IMPORT_TEST_2026_10_07_A"


def _write(path: Path, text: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "jarvis.db"


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """An Obsidian-style vault with nesting, links, settings and junk."""
    root = tmp_path / "AI DATA"
    _write(root / ".obsidian" / "app.json", '{"theme": "dark"}')
    _write(
        root / "Projects" / "Falcon.md",
        "---\ntags: [project, falcon]\n---\n# Falcon\n\n"
        f"The launch code word is {MARKER}. See [[People/Ada]].\n",
    )
    _write(root / "People" / "Ada.md", "# Ada\n\nAda leads [[Falcon]].\n")
    _write(root / "notes.txt", "Groceries: oat milk and lemons.")
    _write(root / "photo.png", b"\x89PNG\r\n\x1a\n\x00\x00")
    _write(root / "attachments" / "scan.md", "# Scan\n\nNot knowledge.\n")
    _write(root / "_archive" / "old.md", "# Old\n\nArchived.\n")
    _write(root / "empty.md", "   \n")
    return root


def _search(vault: Path, db_path: Path, query: str) -> list[str]:
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        return [hit.title for hit in VaultSearch(vault, conn=conn).search(query, k=5)]
    finally:
        conn.close()


def test_a_folder_lands_under_imports_with_paths_frontmatter_and_links_kept(
    source: Path, vault: Path, db_path: Path
) -> None:
    result = run_import(source, vault, db_path=db_path)

    assert result.phase == "done"
    assert result.obsidian_vault is True
    root = vault / IMPORT_DIR / source_label(source.resolve())
    falcon = root / "Projects" / "Falcon.md"
    assert falcon.read_text(encoding="utf-8") == (source / "Projects" / "Falcon.md").read_text(
        encoding="utf-8"
    ), "Markdown is copied byte-for-byte: frontmatter, tags and wikilinks survive"
    assert (root / "People" / "Ada.md").is_file()
    assert (root / "notes.md").read_text(encoding="utf-8").startswith("# notes\n\nGroceries")
    assert not (root / "attachments").exists() and not (root / "_archive").exists()
    assert not any(p.name == "app.json" for p in vault.rglob("*")), ".obsidian is not knowledge"
    assert result.imported == 3 and result.updated == 0 and result.failed == 0
    assert result.skip_reasons == {"unsupported type": 1, "empty": 1}
    assert result.extensions[".md"] == 3
    assert result.indexed == 3


def test_the_imported_fact_is_searchable_without_a_reindex(
    source: Path, vault: Path, db_path: Path
) -> None:
    run_import(source, vault, db_path=db_path)

    assert _search(vault, db_path, MARKER) == ["Falcon"]


def test_reimporting_the_same_source_changes_nothing_and_an_edit_updates_one_page(
    source: Path, vault: Path, db_path: Path
) -> None:
    run_import(source, vault, db_path=db_path)
    pages_before = sorted(p.relative_to(vault) for p in vault.rglob("*.md"))

    again = run_import(source, vault, db_path=db_path)
    assert (again.imported, again.updated, again.unchanged) == (0, 0, 3)
    assert sorted(p.relative_to(vault) for p in vault.rglob("*.md")) == pages_before

    _write(source / "People" / "Ada.md", "# Ada\n\nAda now leads Kestrel_Update_Marker.\n")
    edited = run_import(source, vault, db_path=db_path)
    assert (edited.imported, edited.updated, edited.unchanged) == (0, 1, 2)
    assert _search(vault, db_path, "Kestrel_Update_Marker") == ["Ada"]


def test_risky_or_unusable_files_are_skipped_and_the_rest_still_imports(
    tmp_path: Path, vault: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "mixed"
    _write(src / "keys.md", "# Keys\n\nOPENAI_API_KEY=sk-proj-" + "a" * 48 + "\n")
    _write(src / "binary.md", b"# x\x00\x01\x02")
    _write(src / "big.md", "# Big\n\n" + "word " * 60)
    _write(src / "broken-frontmatter.md", "---\ntitle: [unclosed\n# Still a note\n\nbody\n")
    _write(src / "fine.md", "# Fine\n\nok\n")
    monkeypatch.setattr(importer, "MAX_FILE_BYTES", 150)

    result = run_import(src, vault, db_path=db_path)

    assert result.imported == 2, "malformed frontmatter is still text and still imports"
    assert result.skip_reasons == {
        "looks like it holds a key or token": 1,
        "binary content": 1,
        "too large": 1,
    }
    copied = "".join(p.read_text(encoding="utf-8") for p in vault.rglob("*.md"))
    assert "sk-proj-" not in copied


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_links_are_not_followed_and_the_vault_is_never_imported_into_itself(
    tmp_path: Path, db_path: Path
) -> None:
    outside = tmp_path / "outside"
    _write(outside / "secret.md", "# Outside\n\nnot part of the source\n")
    src = tmp_path / "src"
    _write(src / "note.md", "# Note\n\nkept\n")
    (src / "linked").symlink_to(outside, target_is_directory=True)
    (src / "linked.md").symlink_to(outside / "secret.md")
    inner_vault = src / "jarvis-vault"
    inner_vault.mkdir()

    result = run_import(src, inner_vault, db_path=db_path)

    assert result.imported == 1
    assert not any("Outside" in p.read_text(encoding="utf-8") for p in inner_vault.rglob("*.md"))
    with pytest.raises(ImportRefused):
        run_import(inner_vault, inner_vault, db_path=db_path)
    with pytest.raises(ImportRefused):
        run_import(inner_vault / IMPORT_DIR, inner_vault, db_path=db_path)


def test_destinations_cannot_escape_the_import_folder(tmp_path: Path) -> None:
    root = tmp_path / "vault" / IMPORT_DIR / "src"
    root.mkdir(parents=True)
    assert destination_for(Path("../../etc/passwd"), root) == root / "etc" / "passwd.md"
    assert destination_for(Path('a:b/c?.txt'), root) == root / "a_b" / "c_.md"


def test_a_cancelled_import_keeps_its_pages_and_a_retry_finishes_without_duplicates(
    tmp_path: Path, vault: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "many"
    for i in range(20):
        _write(src / f"note-{i:02d}.md", f"# Note {i}\n\nfact number {i}\n")
    cancel = threading.Event()
    real = importer._import_one
    seen: list[int] = []

    def stop_after_five(*args, **kwargs):
        real(*args, **kwargs)
        seen.append(1)
        if len(seen) == 5:
            cancel.set()

    monkeypatch.setattr(importer, "_import_one", stop_after_five)
    first = run_import(src, vault, db_path=db_path, cancel=cancel)
    monkeypatch.setattr(importer, "_import_one", real)

    assert first.phase == "cancelled" and first.imported == 5
    assert len(list(vault.rglob("*.md"))) == 5
    assert first.indexed == 5, "work done before the cancel is indexed"

    retry = run_import(src, vault, db_path=db_path)
    assert (retry.imported, retry.unchanged) == (15, 5)
    assert len(list(vault.rglob("*.md"))) == 20


def test_only_one_import_runs_at_a_time(
    source: Path, vault: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = threading.Event()
    real = importer.run_import

    def held(*args, **kwargs):
        gate.wait(5)
        return real(*args, **kwargs)

    monkeypatch.setattr(importer, "run_import", held)
    jobs = ImportJobs()
    job = jobs.start(source, vault, db_path=db_path)
    with pytest.raises(RuntimeError):
        jobs.start(source, vault, db_path=db_path)
    gate.set()
    job.thread.join(5)
    assert job.progress.phase == "done"


def test_the_memory_orb_routes_import_report_progress_and_show_the_pages(
    source: Path, vault: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.ui.web.wiki_routes import router

    monkeypatch.setattr(importer, "IMPORT_JOBS", ImportJobs())
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(
        wiki_integration=SimpleNamespace(vault_root=vault),
        memory=SimpleNamespace(data_dir=db_path.parent),
    )
    with TestClient(app) as client:
        started = client.post("/api/wiki/import", json={"path": str(source)})
        assert started.status_code == 200, started.text
        job_id = started.json()["job_id"]
        deadline = time.monotonic() + 10
        while True:
            state = client.get(f"/api/wiki/import/{job_id}").json()
            if not state["running"] or time.monotonic() > deadline:
                break
            time.sleep(0.05)
        assert state["phase"] == "done"
        assert (state["imported"], state["skipped"], state["failed"]) == (3, 2, 0)
        assert state["obsidian_vault"] is True

        titles = {node["title"] for node in client.get("/api/wiki/graph").json()["nodes"]}
        assert {"Falcon", "Ada"} <= titles

        refused = client.post("/api/wiki/import", json={"path": str(vault)})
        assert refused.status_code == 400
        missing = client.post("/api/wiki/import", json={"path": str(source / "nope")})
        assert missing.status_code == 400
        assert client.get("/api/wiki/import/unknown").status_code == 404
