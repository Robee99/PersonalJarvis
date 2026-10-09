"""Nonsecret fixture paths for optional native Hermes cross-runtime tests."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--jarvis-test-python", help="Isolated Jarvis interpreter for the MCP fixture")
    parser.addoption(
        "--hermes-runtime-site",
        help="Installed Hermes dependency directory for Windows .pth bootstrap",
    )
