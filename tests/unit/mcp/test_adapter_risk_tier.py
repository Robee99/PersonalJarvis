"""Risky MCP calls ask first instead of running at the static ``monitor`` tier.

Desktop, filesystem and shell MCP servers (Windows-MCP, Desktop Commander,
the filesystem server) can delete files or run any command. Every MCP tool
used to run at the adapter's static tier, so such a call ran without a word.
``risk_tier_for_args`` escalates those calls to ``ask`` while ordinary
clicks, reads and navigation keep running without a prompt.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.config import SafetyConfig
from jarvis.mcp.adapter import MCPToolAdapter
from jarvis.mcp.client import _tool_to_dict
from jarvis.safety.risk_tier import RiskTierEvaluator


def _adapter(name: str, annotations: dict[str, Any] | None = None) -> MCPToolAdapter:
    client = SimpleNamespace(spec=SimpleNamespace(name="desktop"))
    tool: dict[str, Any] = {"name": name, "description": "A tool.", "inputSchema": {}}
    if annotations is not None:
        tool["annotations"] = annotations
    return MCPToolAdapter(client, tool)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "name",
    ["Click-Tool", "Move-Tool", "browser_navigate", "read_file", "list_directory", "Scrape-Tool"],
)
def test_plain_tools_keep_their_tier(name: str) -> None:
    assert _adapter(name).risk_tier_for_args({}) is None


@pytest.mark.parametrize(
    "name",
    ["delete_file", "write_file", "edit_block", "killProcess", "move_file", "send_email"],
)
def test_consequential_names_ask(name: str) -> None:
    assert _adapter(name).risk_tier_for_args({}) == "ask"


def test_destructive_hint_asks() -> None:
    assert _adapter("do_thing", {"destructiveHint": True}).risk_tier_for_args({}) == "ask"


def test_read_only_hint_wins_over_name() -> None:
    adapter = _adapter("write_report_preview", {"readOnlyHint": True})
    assert adapter.risk_tier_for_args({}) is None


@pytest.mark.parametrize(
    "args",
    [
        {"command": "Remove-Item C:\\temp -Recurse"},
        {"cmd": "rm -rf /tmp/x"},
        {"commands": ["echo hi", "del notes.txt"]},
    ],
)
def test_destructive_command_argument_asks(args: dict[str, Any]) -> None:
    assert _adapter("Powershell-Tool").risk_tier_for_args(args) == "ask"


def test_harmless_command_argument_keeps_tier() -> None:
    assert _adapter("Powershell-Tool").risk_tier_for_args({"command": "Get-Process"}) is None


def test_evaluator_applies_the_escalation() -> None:
    adapter = _adapter("Powershell-Tool")
    evaluator = RiskTierEvaluator(SafetyConfig())
    asked = evaluator.evaluate(adapter, {"command": "del notes.txt"})
    assert asked.tier == "ask"
    assert evaluator.needs_user_confirmation(asked)
    assert evaluator.evaluate(adapter, {"command": "dir"}).tier == "monitor"


def test_client_keeps_server_annotations() -> None:
    annotations = SimpleNamespace(
        model_dump=lambda exclude_none=True: {"destructiveHint": True},
    )
    tool = SimpleNamespace(name="x", description="d", inputSchema={}, annotations=annotations)
    assert _tool_to_dict(tool)["annotations"] == {"destructiveHint": True}
    bare = SimpleNamespace(name="x", description="d", inputSchema={})
    assert _tool_to_dict(bare)["annotations"] == {}
