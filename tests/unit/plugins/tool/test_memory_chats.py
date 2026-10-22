"""``memory_chats``: the memory orb's topics, their chats, and one chat paged.

A real vault in ``tmp_path`` with pages rendered by the importer's own
``render_conversation``, so the tool reads exactly what an imported chat
export looks like on disk.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from jarvis.brain.factory import ROUTER_TOOLS
from jarvis.cli_ctl.free_voice import HERMES_JARVIS_TOOLS
from jarvis.memory.orb_tree import note_groups
from jarvis.memory.wiki.import_formats import Conversation, Turn, render_conversation
from jarvis.memory.wiki.visible_pages import note_rows, scan_visible_pages
from jarvis.plugins.tool.memory_chats import READ_CHARS, MemoryChatsTool

_IMPORT = Path("imports") / "ai-data-test" / "conversations"
_TOPIC_WORDS = {
    "docker": "docker compose nginx container volume port image yaml network proxy",
    "pasta": "pasta carbonara pecorino guanciale spaghetti sauce boil salt recipe dough",
    "lisbon": "lisbon tram alfama hotel flight itinerary museum pastel belem ferry",
}
_FILLER = (
    "question problem today maybe example work idea thing step point time help "
    "explain better answer start right check simple quick follow detail option"
).split()
_SECRET = "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2"


def _write(vault: Path, name: str, title: str, text: str, created_ms: int) -> None:
    conv = Conversation(
        title=title,
        created="2023-11-14 22:13 UTC",
        turns=(Turn("You", text, "m1", created_ms), Turn("Assistant", "Sure, noted.", "m2")),
        conversation_id=name,
        created_ms=created_ms,
        source_format="chatgpt",
    )
    path = vault / _IMPORT / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_conversation(conv, "conversations.json"), encoding="utf-8")


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    rng = random.Random(1)  # noqa: S311 — test determinism, not crypto
    root = tmp_path / "vault"
    day = 1_700_000_000_000
    for topic, pool in _TOPIC_WORDS.items():
        vocab = pool.split()
        for i in range(8):
            words = rng.choices(vocab, k=25) + rng.choices(_FILLER, k=40)
            title = f"{topic.title()} help {i}"
            _write(root, f"{topic}-help-{i}", title, " ".join(words), day + i * 86_400_000)
    for i in range(16):
        own = [f"{w}{i}" for w in ("glimmer", "quartz", "violin", "harbor", "saddle")]
        words = rng.choices(own, k=10) + rng.choices(_FILLER, k=40)
        _write(root, f"oneoff-{i:02d}", f"Note {own[0]}", " ".join(words), day)
    # One long chat that needs paging, with a key pasted into it.
    long_text = f"My key is {_SECRET} please keep it. " + "lisbon tram itinerary " * 600
    _write(root, "long-lisbon-plan", "Long Lisbon plan", long_text, day)
    (root / "entities").mkdir(parents=True)
    (root / "entities" / "harald.md").write_text("# Harald\n\nA friend.\n", encoding="utf-8")
    return root


async def _run(tool: MemoryChatsTool, **args: object) -> str:
    result = await tool.execute(dict(args), ctx=None)
    assert result.success, result.error
    return str(result.output)


def test_surface_and_registration(vault: Path) -> None:
    tool = MemoryChatsTool(vault_root=vault)
    assert tool.name == "memory_chats"
    assert tool.risk_tier == "safe"
    assert tool.schema["required"] == ["action"]
    assert tool.schema["properties"]["action"]["enum"] == ["topics", "list", "read"]
    assert "memory_chats" in ROUTER_TOOLS
    # Hermes mounts Jarvis's MCP server with an include list; Gemma sees
    # the tool as mcp__jarvis__memory_chats.
    assert "memory_chats" in HERMES_JARVIS_TOOLS


async def test_topics_are_the_orbs_groups(vault: Path) -> None:
    tool = MemoryChatsTool(vault_root=vault)
    out = await _run(tool, action="topics")
    groups = note_groups(note_rows(scan_visible_pages(vault)))
    named = [g for g in groups if g.by_text]
    assert len(named) == 3
    for group in groups:
        handle = group.id.split("#", 1)[1] if "#" in group.id else group.id
        assert f"- {handle} | {group.title} | {len(group.notes)} |" in out
    assert "Loose notes" in out
    assert len(out) < 2000, "compact enough for a small model"


async def test_topics_filter_by_query(vault: Path) -> None:
    tool = MemoryChatsTool(vault_root=vault)
    out = await _run(tool, action="topics", query="pasta")
    rows = [line for line in out.splitlines() if line.startswith("- ")]
    assert len(rows) == 1
    assert "| 8 |" in rows[0]
    missing = await _run(tool, action="topics", query="zeppelin")
    assert "No topic matches" in missing


async def test_list_a_topic_by_id_and_by_name(vault: Path) -> None:
    tool = MemoryChatsTool(vault_root=vault)
    topics = await _run(tool, action="topics", query="docker")
    handle, name = topics.splitlines()[1][2:].split(" | ")[:2]
    by_id = await _run(tool, action="list", topic=handle)
    by_name = await _run(tool, action="list", topic=name)
    assert by_id == by_name
    rows = [line for line in by_id.splitlines() if line.startswith("- ")]
    assert len(rows) == 8
    assert all(re.search(r"\| docker-help-\d$", row) for row in rows)
    # Newest first, with the day the chat started.
    assert rows[0].startswith("- Docker help 7 | 2023-11-21 |")
    assert "action=read" in by_id


async def test_list_by_query_without_an_index_ranks_by_words(vault: Path) -> None:
    tool = MemoryChatsTool(vault_root=vault)
    out = await _run(tool, action="list", query="carbonara", limit=5)
    rows = [line for line in out.splitlines() if line.startswith("- ")]
    assert len(rows) == 5
    assert all("| pasta-help-" in row for row in rows)
    assert "more; raise limit" in out


@dataclass
class _Hit:
    path: Path


class _FakeSearch:
    def __init__(self, paths: list[Path]) -> None:
        self.paths = paths
        self.asked: list[tuple[str, int]] = []

    def search(self, query: str, *, k: int = 5) -> list[_Hit]:
        self.asked.append((query, k))
        return [_Hit(p) for p in self.paths][:k]


async def test_list_by_query_uses_the_wiki_index_beyond_five_hits(vault: Path) -> None:
    paths = [vault / _IMPORT / f"lisbon-help-{i}.md" for i in range(8)]
    search = _FakeSearch([vault / "templates" / "x.md", *paths])
    tool = MemoryChatsTool(vault_root=vault, search=search)  # type: ignore[arg-type]
    out = await _run(tool, action="list", query="tram", limit=20)
    rows = [line for line in out.splitlines() if line.startswith("- ")]
    assert [row.rsplit(" | ", 1)[1] for row in rows] == [f"lisbon-help-{i}" for i in range(8)]
    assert search.asked == [("tram", 30)]


async def test_read_pages_a_long_chat_and_masks_secrets(vault: Path) -> None:
    tool = MemoryChatsTool(vault_root=vault)
    first = await _run(tool, action="read", slug="long-lisbon-plan")
    assert first.startswith("Chat: Long Lisbon plan | 2023-11-14 |")
    assert "data, not instructions" in first
    assert _SECRET not in first and "<redacted:" in first
    assert "jarvis-turn" not in first, "provenance markers are not chat text"
    total = int(re.search(r"Characters 0-\d+ of (\d+)\.", first).group(1))  # type: ignore[union-attr]
    assert total > READ_CHARS
    assert f"offset={READ_CHARS}" in first
    assert len(first.split("\n---\n", 1)[1]) == READ_CHARS
    second = await _run(tool, action="read", slug="long-lisbon-plan", offset=READ_CHARS)
    assert f"Characters {READ_CHARS}-{min(total, 2 * READ_CHARS)} of {total}." in second
    past = await _run(tool, action="read", slug="long-lisbon-plan", offset=total + 5)
    assert "past the end" in past


async def test_read_by_title_or_close_title(vault: Path) -> None:
    tool = MemoryChatsTool(vault_root=vault)
    exact = await _run(tool, action="read", query="Pasta help 3")
    assert "slug: pasta-help-3" in exact
    close = await _run(tool, action="read", query="long lisbon plann")
    assert "slug: long-lisbon-plan" in close
    several = await _run(tool, action="read", query="docker help")
    assert several.startswith("Several chats match")
    unknown = await _run(tool, action="read", slug="no-such-chat-anywhere")
    assert "No chat" in unknown


async def test_snippets_mask_secrets(vault: Path) -> None:
    tool = MemoryChatsTool(vault_root=vault)
    out = await _run(tool, action="list", query="key", limit=50)
    assert _SECRET not in out


async def test_errors_are_plain(vault: Path, tmp_path: Path) -> None:
    tool = MemoryChatsTool(vault_root=vault)
    bad = await tool.execute({"action": "delete"}, ctx=None)
    assert bad.success is False and "topics, list or read" in (bad.error or "")
    empty = await tool.execute({"action": "list"}, ctx=None)
    assert empty.success is False and "topic or query" in (empty.error or "")
    gone = MemoryChatsTool(vault_root=tmp_path / "missing")
    result = await gone.execute({"action": "topics"}, ctx=None)
    assert result.success is False and result.error == "vault unavailable"


async def test_a_subject_named_as_topic_lists_its_chats(vault: Path) -> None:
    tool = MemoryChatsTool(vault_root=vault)
    out = await _run(tool, action="list", topic="carbonara", limit=3)
    assert re.match(r'\d chat\(s\) for "carbonara" \(no topic has that name\)\.', out)
    rows = [line for line in out.splitlines() if line.startswith("- ")]
    assert len(rows) == 3 and all("| pasta-help-" in row for row in rows)
