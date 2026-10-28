"""Chats saved from Chrome into Downloads/AI Chats reach memory on their own."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from jarvis.memory.wiki.chat_sync import chat_folders, sync_once
from jarvis.memory.wiki.importer import IMPORT_DIR

CHAT = (
    "---\ntitle: Heron plan\nplatform: claude\nconversation_id: 0c1d-heron\n---\n\n"
    "# Heron plan\n\nThe Heron_Marker plan.\n"
)


def test_the_chat_folders_are_downloads_and_documents(tmp_path: Path) -> None:
    assert chat_folders(tmp_path) == [
        tmp_path / "Downloads" / "AI Chats",
        tmp_path / "Documents" / "AI Chats",
    ]


def test_a_saved_chat_is_imported_once_and_again_only_when_something_changed(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    db_path = tmp_path / "data" / "jarvis.db"
    chats = tmp_path / "Downloads" / "AI Chats"
    note = chats / "Heron plan (Claude)" / "Heron plan.md"
    note.parent.mkdir(parents=True)
    note.write_text(CHAT, "utf-8")
    missing = tmp_path / "Documents" / "AI Chats"

    first = asyncio.run(sync_once(vault, db_path, folders=[chats, missing]))
    second = asyncio.run(sync_once(vault, db_path, folders=[chats, missing]))
    note.write_text(CHAT.replace("Heron_Marker", "Egret_Marker"), "utf-8")
    later = note.stat().st_mtime + 5
    os.utime(note, (later, later))
    third = asyncio.run(sync_once(vault, db_path, folders=[chats, missing]))

    assert first == [str(chats)]
    assert second == []
    assert third == [str(chats)]
    (page,) = (vault / IMPORT_DIR).rglob("Heron plan.md")
    assert "Egret_Marker" in page.read_text("utf-8")
