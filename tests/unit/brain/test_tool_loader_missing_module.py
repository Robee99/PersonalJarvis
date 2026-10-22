"""A tool whose Jarvis module is missing is logged as a warning.

The frozen build once left out ``jarvis.mcp.loader``; the brain then dropped
every MCP tool and the only trace was a DEBUG line nobody sees.
"""

from __future__ import annotations

import logging
from importlib.metadata import EntryPoint

import pytest

from jarvis.brain import factory


def _load(monkeypatch: pytest.MonkeyPatch, target: str) -> dict:
    ep = EntryPoint(name="mcp-tools", value=target, group="jarvis.tool")
    monkeypatch.setattr("importlib.metadata.entry_points", lambda group=None: [ep])
    return factory._load_tools_for_tier(
        "router", bus=None, executor=None, harness_manager=None,
        user_profile=None, people=None, config=None,
    )


def test_a_missing_jarvis_module_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger=factory.log.name):
        _load(monkeypatch, "jarvis.mcp.not_bundled:McpToolLoader")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("mcp-tools" in r.getMessage() for r in warnings)


def test_a_missing_optional_package_stays_quiet(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger=factory.log.name):
        _load(monkeypatch, "some_optional_extra.tool:Tool")
    loud = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not [r for r in loud if "not loadable" in r.getMessage()]
