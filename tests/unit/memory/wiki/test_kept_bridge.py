"""Chats from the Kept extension land in Jarvis memory, one page per chat."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from jarvis.memory.wiki.importer import IMPORT_DIR, ChatSaver, run_import
from jarvis.memory.wiki.kept_bridge import build_app, load_token

TOKEN = "t" * 43


def _kept_markdown(text: str, synced: str = "2026-10-26T20:00:00Z") -> str:
    return (
        '---\nid: "6703a1b2-falcon"\nplatform: "chatgpt"\ntitle: "Falcon launch"\n'
        f"synced: {synced}\nmessages: 2\ntags:\n  - \"kept/chatgpt\"\n---\n\n"
        f"# Falcon launch\n\n### User\n\nWhen?\n\n### Assistant\n\n{text}\n"
    )


def _payload(text: str, synced: str = "2026-10-26T20:00:00Z") -> dict[str, object]:
    return {
        "conversation_id": "6703a1b2-falcon",
        "platform": "chatgpt",
        "title": "Falcon launch",
        "created_at": "2026-10-20T08:00:00Z",
        "messages": [],
        "markdown": _kept_markdown(text, synced),
    }


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    vault = tmp_path / "vault"
    vault.mkdir()
    saver = ChatSaver(vault, tmp_path / "data" / "jarvis.db")
    client = TestClient(build_app(saver, TOKEN), base_url="http://127.0.0.1:18241")
    return client, vault


def test_the_extension_connects_with_the_token_from_the_connect_page(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    page = client.get("/connect").text
    assert re.search(r'<meta name="kept-token" content="([^"]+)"', page).group(1) == TOKEN
    assert client.get("/api/ping").json()["status"] == "ok"
    assert client.post("/api/extension_ping").status_code == 401
    auth = {"Authorization": f"Bearer {TOKEN}"}
    first = client.post("/api/extension_ping", headers=auth).json()
    later = client.post("/api/extension_ping", headers=auth).json()
    assert first == {"sync_requested": True, "restricted_mode": False}
    assert later["sync_requested"] is False


def test_other_hosts_are_refused(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    assert client.get("/connect", headers={"host": "evil.example:18241"}).status_code == 403


def test_a_synced_chat_is_one_page_updated_as_it_grows(tmp_path: Path) -> None:
    client, vault = _client(tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}"}

    first = client.post("/api/ingest", json=_payload("On Heron_Day."), headers=auth).json()
    again = client.post(
        "/api/ingest", json=_payload("On Heron_Day.", synced="2026-10-27T01:00:00Z"), headers=auth
    ).json()
    grown = client.post("/api/ingest", json=_payload("Moved to Egret_Day."), headers=auth).json()

    statuses = [first["status"], again["status"], grown["status"]]
    assert statuses == ["created", "unchanged", "updated"]
    (page,) = (vault / IMPORT_DIR / "Kept" / "chatgpt").glob("*.md")
    assert page.name == "2026-10-20_falcon-launch.md"
    assert "Egret_Day" in page.read_text("utf-8")
    assert client.post("/api/ingest", json=_payload("x")).status_code == 401


def test_a_chat_already_imported_from_an_export_is_updated_not_repeated(tmp_path: Path) -> None:
    client, vault = _client(tmp_path)
    saved = tmp_path / "AI Chats" / "Falcon launch.md"
    saved.parent.mkdir()
    saved.write_text(_kept_markdown("On Heron_Day."), "utf-8")
    run_import(saved.parent, vault, db_path=None)

    result = client.post(
        "/api/ingest", json=_payload("Moved to Egret_Day."),
        headers={"Authorization": f"Bearer {TOKEN}"},
    ).json()

    pages = list((vault / IMPORT_DIR).rglob("*.md"))
    assert result["status"] == "updated"
    assert len(pages) == 1 and "Egret_Day" in pages[0].read_text("utf-8")


def test_the_token_is_made_once_and_kept(tmp_path: Path) -> None:
    token = load_token(tmp_path)
    assert len(token) >= 32 and load_token(tmp_path) == token
