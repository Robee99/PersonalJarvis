from __future__ import annotations

import asyncio
from pathlib import Path

from jarvis.core.config import WikiIntegrationConfig
from jarvis.plugins.tool.kept_archive_search import KeptArchiveSearchTool


def _run(tool: KeptArchiveSearchTool, query: str):
    return asyncio.run(tool.execute({"query": query}, None))


def test_kept_archive_is_disabled_without_explicit_path() -> None:
    assert WikiIntegrationConfig().kept_archive_root is None
    result = _run(KeptArchiveSearchTool(), "anything")
    assert not result.success
    assert "disabled" in (result.error or "")


def test_search_returns_archive_relative_source_and_never_writes(tmp_path: Path) -> None:
    archive = tmp_path / "kept"
    note = archive / "chatgpt" / "decisions.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "# Local model decision\nWe selected Qwen vision for the JARVIS workstation.\n",
        encoding="utf-8",
    )
    before = note.read_bytes()

    result = _run(KeptArchiveSearchTool(archive), "Qwen workstation")

    assert result.success
    assert "Local model decision" in result.output
    assert "chatgpt/decisions.md" in result.output
    assert "[archived text]" in result.output
    assert "untrusted source text" in result.output
    assert note.read_bytes() == before
    assert list(archive.rglob("*.md")) == [note]


def test_search_does_not_follow_symlink_outside_archive(tmp_path: Path) -> None:
    archive = tmp_path / "kept"
    archive.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Secret\nneedle should not leak\n", encoding="utf-8")
    link = archive / "outside.md"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        return

    result = _run(KeptArchiveSearchTool(archive), "needle")

    assert result.success
    assert "No Kept archive matches" in result.output
    assert "Secret" not in result.output


def test_kept_tool_is_reachable_only_on_the_router_tier() -> None:
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "kept-archive-search" in ROUTER_TOOLS