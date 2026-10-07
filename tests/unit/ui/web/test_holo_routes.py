"""HOLO deck routes: the page under /holo, orbs from the vault, no file escapes it."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web import holo_routes
from jarvis.ui.web.surface_security import _is_static_request


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "projects").mkdir(parents=True)
    (root / "concepts").mkdir()
    (root / ".obsidian").mkdir()
    (root / "templates").mkdir()
    (root / "projects" / "old.md").write_text("# Old project\nfirst line\n", encoding="utf-8")
    newer = root / "projects" / "jarvis.md"
    newer.write_text(
        "---\ntype: project\n---\n# Jarvis build\nLocal Qwen on 8 GB.\n## Notes\nArrow done.\n",
        encoding="utf-8",
    )
    os.utime(root / "projects" / "old.md", (1_000_000, 1_000_000))
    (root / "templates" / "t.md").write_text("# {{title}}\n", encoding="utf-8")
    (root / ".obsidian" / "x.md").write_text("# hidden\n", encoding="utf-8")
    (root / "index.md").write_text("# Index\nwelcome\n", encoding="utf-8")
    return root


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(holo_routes.router)
    app.include_router(holo_routes.api_router)
    app.state.config = SimpleNamespace(
        wiki_integration=SimpleNamespace(vault_root=str(_vault(tmp_path)))
    )

    # Stand-in for the app's SPA catch-all, which must never answer for HOLO.
    @app.get("/{full_path:path}")
    def spa(full_path: str) -> dict[str, str]:
        return {"spa": full_path}

    return TestClient(app)


def test_page_is_served_with_its_paths_under_holo(client: TestClient) -> None:
    for url in ("/holo/", "/holo"):
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        html = response.text
        assert "fetch('/api/holo/tree')" in html
        assert "fetch('/api/tree')" not in html
        assert "'/vendor/" not in html and '"/vendor/' not in html


def test_vault_folders_become_orbs_newest_page_first(client: TestClient) -> None:
    orbs = client.get("/api/holo/tree").json()

    assert [o["name"] for o in orbs] == ["PROJECTS", "NOTES"]
    projects = orbs[0]["files"]
    assert [f["name"] for f in projects] == ["jarvis.md", "old.md"]
    card = projects[0]
    assert card["title"] == "Jarvis build"
    assert card["body"] == "Local Qwen on 8 GB.\nArrow done."
    assert "type: project" not in card["full"]
    assert orbs[1]["files"][0]["title"] == "Index"


def test_missing_vault_gives_no_orbs() -> None:
    assert holo_routes.vault_orbs(None) == []
    assert holo_routes.vault_orbs(Path("/does/not/exist")) == []


def test_unshipped_assets_404_instead_of_the_spa(client: TestClient) -> None:
    assert client.get("/holo/vendor/vision_bundle.mjs").status_code == 404
    assert client.get("/holo/props/triceratops.glb").status_code == 404
    assert client.get("/api/holo/props").json() == []


def test_state_and_diag_are_acknowledged(client: TestClient) -> None:
    assert client.post("/api/holo/state", json={"pinned": 1}).json() == {"ok": True}
    assert client.post("/api/holo/diag", content=b"x" * 10_000).json() == {"ok": True}


def test_orb_count_and_cards_are_bounded(tmp_path: Path) -> None:
    root = tmp_path / "big"
    for i in range(holo_routes.MAX_ORBS + 3):
        folder = root / f"f{i:02d}"
        folder.mkdir(parents=True)
        for j in range(holo_routes.MAX_FILES_PER_ORB + 2):
            (folder / f"p{j}.md").write_text(f"# P{j}\nbody\n", encoding="utf-8")

    orbs = holo_routes.vault_orbs(root)

    assert len(orbs) == holo_routes.MAX_ORBS
    assert all(len(o["files"]) == holo_routes.MAX_FILES_PER_ORB for o in orbs)


def test_vendored_page_keeps_its_mit_licence() -> None:
    licence = (holo_routes.PAGE.parent / "LICENSE").read_text(encoding="utf-8")
    assert licence.startswith("MIT License")
    assert "Zubair Trabzada" in licence


def _pinned(monkeypatch: pytest.MonkeyPatch, files: dict[str, bytes]) -> None:
    """Pin VENDOR_FILES to small known bodies so no test downloads 32 MB."""
    monkeypatch.setattr(
        holo_routes,
        "VENDOR_FILES",
        {name: hashlib.sha256(body).hexdigest() for name, body in files.items()},
    )


def _tree(root: Path) -> list[Path]:
    return list(root.rglob("*"))


def _github(files: dict[str, bytes], requested: list[str]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        name = request.url.path.split("/vendor/", 1)[1]
        return httpx.Response(200, content=files[name])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_install_downloads_pinned_files_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {"vision_bundle.mjs": b"export {}", "wasm/vision_wasm_internal.wasm": b"\0asm"}
    _pinned(monkeypatch, files)
    requested: list[str] = []

    async with _github(files, requested) as client:
        await holo_routes.install_vendor_files(tmp_path, client)
        await holo_routes.install_vendor_files(tmp_path, client)

    assert (tmp_path / "wasm" / "vision_wasm_internal.wasm").read_bytes() == b"\0asm"
    assert holo_routes.missing_vendor_files(tmp_path) == []
    assert len(requested) == 2
    assert all(f"/{holo_routes.HOLO_COMMIT}/vendor/" in path for path in requested)


async def test_tampered_download_is_never_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pinned(monkeypatch, {"vision_bundle.mjs": b"export {}"})

    async with _github({"vision_bundle.mjs": b"evil()"}, []) as client:
        with pytest.raises(ValueError, match="pinned digest"):
            await holo_routes.install_vendor_files(tmp_path, client)

    assert _tree(tmp_path) == []


def test_installed_files_are_served_with_their_types(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vendor"
    files = {"vision_bundle.mjs": b"export {}", "wasm/vision_wasm_internal.wasm": b"\0asm"}
    _pinned(monkeypatch, files)
    monkeypatch.setattr(holo_routes, "vendor_dir", lambda: root)
    assert client.get("/api/holo/install").json() == {"ready": False, "missing": list(files)}
    for name, body in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(body)
    (root / "secret.txt").write_text("not pinned", encoding="utf-8")

    bundle = client.get("/holo/vendor/vision_bundle.mjs")
    wasm = client.get("/holo/vendor/wasm/vision_wasm_internal.wasm")

    assert bundle.headers["content-type"].startswith("text/javascript")
    assert wasm.headers["content-type"] == "application/wasm"
    assert wasm.content == b"\0asm"
    assert client.get("/holo/vendor/secret.txt").status_code == 404
    assert client.get("/holo/vendor/../vendor/secret.txt").status_code == 404
    assert client.get("/api/holo/install").json() == {"ready": True, "missing": []}


def test_vault_and_install_routes_sit_behind_the_session_check() -> None:
    # SurfaceSecurity leaves non-/api GETs open for static files; the notes
    # and the download trigger must never be one of them.
    for route in holo_routes.api_router.routes:
        assert not _is_static_request(route.path, "GET"), route.path
    for route in holo_routes.router.routes:
        assert route.methods == {"GET"}, route.path
        assert route.endpoint not in (holo_routes.tree, holo_routes.install_status)
