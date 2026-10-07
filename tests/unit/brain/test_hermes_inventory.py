"""Hermes Agent capability inventory: runtime discovery, provenance, honest errors."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from jarvis.plugins.brain import hermes_inventory as inv


def _skill(root: Path, rel: str, name: str | None = None) -> Path:
    folder = root / rel
    folder.mkdir(parents=True, exist_ok=True)
    front = f"---\nname: {name}\ndescription: x\n---\n" if name else "# no frontmatter\n"
    (folder / "SKILL.md").write_text(front + "body\n", encoding="utf-8")
    return folder


@pytest.fixture
def hermes_home(tmp_path: Path) -> Path:
    home = tmp_path / "hermes"
    skills = home / "skills"
    # bundled (manifest), hub (lock key + install_path), local/agent-created
    _skill(skills, "productivity/notes", "notes")
    _skill(skills, "dev/git-helper", "git-helper")
    (skills / ".bundled_manifest").write_text("notes:abc123\ngit-helper:def456\n", encoding="utf-8")
    _skill(skills, "hub-one", "hub-one")
    _skill(skills, "vendor/pathy", "renamed-by-frontmatter")
    (skills / ".hub").mkdir()
    (skills / ".hub" / "lock.json").write_text(
        json.dumps({"installed": {"hub-one": {}, "pathy": {"install_path": "vendor/pathy"}}}),
        encoding="utf-8",
    )
    _skill(skills, "my-own")  # no frontmatter: folder name
    # never skills: metadata dirs and support folders inside a skill package
    _skill(skills, ".archive/old", "archived")
    _skill(skills, "my-own/references/inner", "inner-ref")
    # external dir (relative to HERMES_HOME) with one clash and one unique skill
    ext = home / "shared-skills"
    _skill(ext, "my-own", "my-own")
    _skill(ext, "team-skill", "team-skill")
    # user plugin with a skill, namespaced by plugin.yaml
    plugin = home / "plugins" / "meet-plugin"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: meet\n", encoding="utf-8")
    _skill(plugin, "skills/join", "join")
    (home / "config.yaml").write_text(
        """
skills:
  external_dirs:
    - shared-skills
    - /does/not/exist
mcp_servers:
  github:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "ghp_SECRET_TOKEN"
  notion:
    url: https://mcp.notion.com/mcp?token=SECRET_QUERY
    headers:
      Authorization: "Bearer SECRET_HEADER"
    enabled: "off"
    tools:
      include: [search, fetch]
""",
        encoding="utf-8",
    )
    (home / ".env").write_text("API_SERVER_KEY=SECRET_API_KEY\n", encoding="utf-8")
    return home


def _toolsets(_home: Path) -> list[dict[str, Any]]:
    return [
        {"name": "web", "label": "Web", "enabled": True, "configured": True, "tools": ["a", "b"]},
        {"name": "browser", "label": "Browser", "enabled": False, "configured": False, "tools": []},
        {"name": "files", "label": "Files", "enabled": True, "configured": True, "tools": ["c"]},
    ]


def _plugins(_home: Path) -> list[dict[str, Any]]:
    return [
        {"name": "meet", "status": "enabled", "version": "1.0", "source": "user"},
        {"name": "kanban", "status": "not enabled", "version": "", "source": "bundled"},
    ]


def _collect(home: Path, **kw: Any) -> dict[str, Any]:
    kw.setdefault("fetch_toolsets", _toolsets)
    kw.setdefault("list_plugins", _plugins)
    return inv.collect_hermes_inventory(home, **kw)


def test_skills_are_counted_by_provenance(hermes_home: Path) -> None:
    skills = _collect(hermes_home)["skills"]
    by_name = {row["name"]: row["provenance"] for row in skills["items"]}
    assert by_name == {
        "notes": "bundled",
        "git-helper": "bundled",
        "hub-one": "hub",
        "renamed-by-frontmatter": "hub",
        "my-own": "local",  # the local copy wins over the external clash
        "team-skill": "external",
        "meet:join": "plugin",
    }
    assert (skills["bundled"], skills["hub"], skills["local"]) == (2, 2, 1)
    assert (skills["external"], skills["plugin"], skills["total"]) == (1, 1, 7)
    assert skills["truncated"] is False


def test_mcp_servers_report_config_without_secrets(hermes_home: Path) -> None:
    result = _collect(hermes_home)
    assert result["mcp_servers"] == [
        {"name": "github", "transport": "stdio", "enabled": True,
         "tools_filter": {"include": None, "exclude": None}},
        {"name": "notion", "transport": "http", "enabled": False,
         "tools_filter": {"include": ["search", "fetch"], "exclude": None}},
    ]
    assert result["mcp_status_checked"] is False
    assert "SECRET" not in json.dumps(result)


def test_plugins_and_toolsets_are_summarised(hermes_home: Path) -> None:
    result = _collect(hermes_home)
    assert result["available"] is True and result["reason"] is None
    assert result["plugins"]["available"] is True
    assert (result["plugins"]["enabled"], result["plugins"]["total"]) == (1, 2)
    toolsets = result["toolsets"]
    assert (toolsets["enabled"], toolsets["total"]) == (2, 3)
    assert toolsets["items"][0] == {
        "name": "web", "label": "Web", "enabled": True, "configured": True, "tool_count": 2,
    }


def test_each_part_fails_on_its_own(hermes_home: Path) -> None:
    def down(_home: Path) -> list[dict[str, Any]]:
        raise inv.InventoryPartError("Hermes API server is not reachable")

    def broken(_home: Path) -> list[dict[str, Any]]:
        raise RuntimeError("boom")

    result = _collect(hermes_home, fetch_toolsets=down, list_plugins=broken)
    assert result["available"] is True
    assert result["skills"]["total"] == 7
    assert result["toolsets"] == {
        "available": False, "enabled": 0, "total": 0, "items": [],
        "error": "Hermes API server is not reachable",
    }
    assert result["plugins"]["available"] is False
    assert result["plugins"]["error"] == "Hermes plugins could not be listed"


def test_missing_home_is_reported_honestly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inv, "find_hermes_home", lambda: None)
    result = inv.collect_hermes_inventory(use_cache=False)
    assert result["available"] is False
    assert result["reason"] == inv.NOT_FOUND_REASON
    assert result["toolsets"]["available"] is False
    assert result["skills"]["total"] == 0


def test_home_is_the_first_existing_candidate(tmp_path: Path) -> None:
    assert inv.find_hermes_home([tmp_path / "nope", tmp_path]) == tmp_path
    assert inv.find_hermes_home([tmp_path / "nope"]) is None


def test_unreadable_config_is_flagged(tmp_path: Path) -> None:
    home = tmp_path / "h"
    home.mkdir()
    (home / "config.yaml").write_text("mcp_servers: [unclosed\n", encoding="utf-8")
    result = _collect(home)
    assert result["config_error"] == "Hermes config.yaml could not be read"
    assert result["mcp_servers"] == []


def test_item_rows_are_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inv, "ITEM_CAP", 2)
    home = tmp_path / "h"
    for i in range(4):
        _skill(home / "skills", f"s{i}", f"s{i}")
    skills = _collect(home)["skills"]
    assert skills["total"] == 4 and len(skills["items"]) == 2 and skills["truncated"] is True


def test_toolsets_http_refuses_non_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.plugins.brain import hermes

    monkeypatch.setattr(hermes, "configured_base_url", lambda: "http://10.0.0.5:8642")
    with pytest.raises(inv.InventoryPartError, match="not on this computer"):
        inv.fetch_toolsets_http(tmp_path)


def test_toolsets_http_sends_key_and_parses(
    hermes_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    from jarvis.plugins.brain import hermes

    seen: dict[str, Any] = {}

    def fake_get(url: str, headers: dict[str, str], timeout: float) -> httpx.Response:
        seen.update(url=url, headers=headers)
        body = {"object": "list", "platform": "api_server", "data": _toolsets(hermes_home)}
        return httpx.Response(200, json=body)

    monkeypatch.setattr(hermes, "configured_base_url", lambda: "http://127.0.0.1:8642")
    monkeypatch.setattr(httpx, "get", fake_get)
    rows = inv.fetch_toolsets_http(hermes_home)
    assert seen["url"] == "http://127.0.0.1:8642/v1/toolsets"
    assert seen["headers"] == {"Authorization": "Bearer SECRET_API_KEY"}
    assert [r["name"] for r in rows] == ["web", "browser", "files"]

    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(500, json={}))
    with pytest.raises(inv.InventoryPartError, match="HTTP 500"):
        inv.fetch_toolsets_http(hermes_home)

    def refuse(*_a: Any, **_k: Any) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", refuse)
    with pytest.raises(inv.InventoryPartError, match="not reachable"):
        inv.fetch_toolsets_http(hermes_home)


def test_plugins_cli_parses_json_and_handles_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.agent_chat import runner_cli

    calls: list[dict[str, Any]] = []
    outputs = iter([
        subprocess.CompletedProcess([], 0, stdout=json.dumps(_plugins(tmp_path)), stderr=""),
        subprocess.CompletedProcess([], 0, stdout="No plugins installed.\n", stderr=""),
        subprocess.CompletedProcess([], 2, stdout="", stderr="bad"),
    ])

    def fake_run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, **kw})
        return next(outputs)

    monkeypatch.setattr(runner_cli, "hermes_argv_prefix", lambda: ["hermes"])
    monkeypatch.setattr(inv.subprocess, "run", fake_run)
    assert [r["name"] for r in inv.list_plugins_cli(tmp_path)] == ["meet", "kanban"]
    assert calls[0]["argv"] == ["hermes", "plugins", "list", "--json"]
    assert calls[0]["timeout"] == inv.PLUGINS_TIMEOUT_S
    assert "creationflags" in calls[0]
    assert calls[0]["env"]["HERMES_HOME"] == str(tmp_path)
    assert inv.list_plugins_cli(tmp_path) == []
    with pytest.raises(inv.InventoryPartError, match="exit 2"):
        inv.list_plugins_cli(tmp_path)


def test_plugins_cli_missing_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.agent_chat import runner_cli

    def missing() -> list[str]:
        raise runner_cli.CliUnavailable("nope")

    monkeypatch.setattr(runner_cli, "hermes_argv_prefix", missing)
    with pytest.raises(inv.InventoryPartError, match="not on PATH"):
        inv.list_plugins_cli(tmp_path)


def test_default_fetchers_are_cached(hermes_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inv.clear_cache()
    calls = {"n": 0}

    def counting(home: Path) -> list[dict[str, Any]]:
        calls["n"] += 1
        return _toolsets(home)

    monkeypatch.setattr(inv, "fetch_toolsets_http", counting)
    monkeypatch.setattr(inv, "list_plugins_cli", _plugins)
    try:
        first = inv.collect_hermes_inventory(hermes_home)
        second = inv.collect_hermes_inventory(hermes_home)
    finally:
        inv.clear_cache()
    assert first is second and calls["n"] == 1


def test_route_returns_inventory(hermes_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from jarvis.ui.web import hermes_inventory_routes as routes

    monkeypatch.setattr(
        routes, "collect_hermes_inventory", lambda: _collect(hermes_home)
    )
    cleared = {"n": 0}
    monkeypatch.setattr(routes, "clear_cache", lambda: cleared.__setitem__("n", cleared["n"] + 1))
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    body = client.get("/api/hermes/inventory").json()
    assert body["available"] is True
    assert body["skills"]["total"] == 7
    assert body["toolsets"]["enabled"] == 2
    assert cleared["n"] == 0
    assert client.get("/api/hermes/inventory?refresh=true").status_code == 200
    assert cleared["n"] == 1


def test_the_catalog_counts_what_hermes_ships(tmp_path: Path) -> None:
    from jarvis.plugins.brain.hermes_inventory import _catalog_part

    install = tmp_path / "hermes-agent"
    for name in ("asana", "notion"):
        (install / "optional-mcps" / name).mkdir(parents=True)
        (install / "optional-mcps" / name / "manifest.yaml").write_text("name: x\n")
    (install / "optional-mcps" / "README-only").mkdir()
    for root, names in (("optional-skills", ("a/one", "a/two", "b/three")), ("skills", ("c/four",))):
        for name in names:
            (install / root / name).mkdir(parents=True)
            (install / root / name / "SKILL.md").write_text("---\nname: s\n---\n")

    part = _catalog_part(tmp_path, lambda _home: install)

    assert part["available"] is True
    assert (part["mcp_servers"], part["optional_skills"], part["bundled_skills"]) == (2, 3, 1)
    assert part["mcp_names"] == ["asana", "notion"]
    missing = _catalog_part(tmp_path, lambda _home: None)
    assert missing["available"] is False and missing["error"]
