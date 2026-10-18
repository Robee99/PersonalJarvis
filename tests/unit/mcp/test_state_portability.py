"""Portable persistence tests for the user-managed MCP registry."""

from __future__ import annotations

import json

import pytest

from jarvis.mcp import state


def test_jarvis_data_dir_owns_mcp_config_on_headless_hosts(
    monkeypatch,
    tmp_path,
) -> None:
    data_dir = tmp_path / "mounted-data"
    legacy_project = tmp_path / "read-only-app" / "mcp.json"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data_dir))
    monkeypatch.delenv("JARVIS_MCP_CONFIG", raising=False)
    monkeypatch.setattr(state, "MCP_JSON_PATH", legacy_project)

    state.save_config({"mcpServers": {"remote": {"enabled": True}}})

    assert not legacy_project.exists()
    assert json.loads((data_dir / "mcp.json").read_text(encoding="utf-8")) == {
        "mcpServers": {"remote": {"enabled": True}}
    }
    assert state.load_config()["mcpServers"]["remote"]["enabled"] is True


def test_unwritable_project_config_crosses_to_per_user_storage(
    monkeypatch,
    tmp_path,
) -> None:
    from pathlib import Path

    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("blocked", encoding="utf-8")
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.delenv("JARVIS_MCP_CONFIG", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user-data"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(state, "MCP_JSON_PATH", blocked_parent / "mcp.json")

    state.save_config({"mcpServers": {"hosted": {"enabled": True}}})

    fallback = tmp_path / "user-data" / "Jarvis" / "mcp.json"
    assert fallback.exists()
    assert state.load_config()["mcpServers"]["hosted"]["enabled"] is True


def test_explicit_mcp_config_path_is_not_silently_redirected(
    monkeypatch,
    tmp_path,
) -> None:
    explicit = tmp_path / "custom" / "servers.json"
    monkeypatch.setenv("JARVIS_MCP_CONFIG", str(explicit))
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))

    state.save_config({"mcpServers": {}})

    assert explicit.exists()
    assert not (tmp_path / "data" / "mcp.json").exists()


def test_mcp_config_saved_with_a_byte_order_mark_still_loads(
    monkeypatch,
    tmp_path,
) -> None:
    # PowerShell's Set-Content -Encoding UTF8 and older Notepad write a BOM;
    # reading it as plain UTF-8 dropped every server the person added.
    config = tmp_path / "mcp.json"
    servers = {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}
    config.write_bytes(b"\xef\xbb\xbf" + json.dumps({"mcpServers": servers}).encode())
    monkeypatch.setenv("JARVIS_MCP_CONFIG", str(config))

    assert state.load_config()["mcpServers"]["fetch"]["command"] == "uvx"


def test_windows_finds_existing_standard_config_when_localappdata_is_sandboxed(
    monkeypatch,
    tmp_path,
) -> None:
    """Packaged launches may expose a sandbox LOCALAPPDATA, not the user's app data."""
    from pathlib import Path

    home = tmp_path / "user"
    sandbox_local = tmp_path / "sandbox" / "AC"
    standard_config = home / "AppData" / "Local" / "Jarvis" / "mcp.json"
    standard_config.parent.mkdir(parents=True)
    standard_config.write_text(
        json.dumps({"mcpServers": {"windows-desktop": {"enabled": True}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("LOCALAPPDATA", str(sandbox_local))
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.delenv("JARVIS_MCP_CONFIG", raising=False)
    monkeypatch.setattr(state, "MCP_JSON_PATH", tmp_path / "repo" / "mcp.json")
    # The fallback only applies on Windows; pretend to be there on any host.
    import os as real_os

    class _WindowsOs:
        name = "nt"

        def __getattr__(self, attr):
            return getattr(real_os, attr)

    monkeypatch.setattr(state, "os", _WindowsOs())

    assert state._active_mcp_json_path() == standard_config
    assert state.load_config()["mcpServers"]["windows-desktop"]["enabled"] is True


@pytest.mark.asyncio
async def test_config_info_reports_the_active_mcp_file(
    monkeypatch,
    tmp_path,
) -> None:
    from jarvis.ui.web.mcp_routes import config_info

    active = tmp_path / "user" / "mcp.json"
    active.parent.mkdir(parents=True)
    active.write_text('{"mcpServers":{"fetch":{"enabled":true}}}', encoding="utf-8")
    monkeypatch.setattr(state, "_active_mcp_json_path", lambda: active)

    result = await config_info()

    assert result["path"] == str(active)
    assert '"fetch"' in result["content"]
