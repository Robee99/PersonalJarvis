"""Tool Armory actions on Hermes: each is one ``hermes`` command, and only for listed items."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.plugins.brain import hermes_actions as act
from jarvis.plugins.brain.hermes_inventory import collect_skills


def _skill(root: Path, rel: str, name: str, description: str = "does a thing") -> None:
    folder = root / rel
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody\n", encoding="utf-8"
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "hermes"
    _skill(home / "skills", "notes", "notes")
    _skill(home / "skills", "creative/ascii-art", "ascii-art")
    (home / "config.yaml").write_text(
        "skills:\n  disabled: [ascii-art]\n"
        "mcp_servers:\n  jarvis:\n    url: http://127.0.0.1:47821/api/control/mcp/\n",
        encoding="utf-8",
    )
    return home


@pytest.fixture
def install(tmp_path: Path) -> Path:
    install = tmp_path / "hermes-agent"
    for name, auth in (("deepwiki", "none"), ("notion", "oauth")):
        folder = install / "optional-mcps" / name
        folder.mkdir(parents=True)
        (folder / "manifest.yaml").write_text(
            f"name: {name}\ndescription: >-\n  {name} for\n  you.\nauth:\n  type: {auth}\n",
            encoding="utf-8",
        )
    _skill(install / "optional-skills", "creative/ascii-art", "ascii-art", "Draw with text")
    _skill(install / "optional-skills", "finance/ledger", "ledger", "Books")
    return install


class Recorder:
    def __init__(self, code: int = 0, output: str = "✓ done\n") -> None:
        self.calls: list[list[str]] = []
        self.code, self.output = code, output

    def __call__(self, args: list[str], _home: Path, _timeout: float) -> act.CommandResult:
        self.calls.append(args)
        return act.CommandResult(self.code, self.output)


def _plugins(_home: Path) -> list[dict[str, Any]]:
    return [{"name": "kanban", "status": "not enabled"}]


def _toolsets(_home: Path) -> list[dict[str, Any]]:
    return [{"name": "web", "enabled": True}]


def _run(home: Path, install: Path, kind: str, op: str, name: str, run: Recorder) -> dict[str, Any]:
    return act.run_action(
        kind,
        op,
        name,
        home,
        run=run,
        list_plugins=_plugins,
        fetch_toolsets=_toolsets,
        find_install=lambda _h: install,
    )


def test_skills_show_whether_hermes_has_them_disabled(home: Path) -> None:
    items = {
        row["name"]: row["enabled"]
        for row in collect_skills(home, {"skills": {"disabled": ["ascii-art"]}})["items"]
    }
    assert items == {"notes": True, "ascii-art": False}


@pytest.mark.parametrize(
    ("kind", "op", "name", "argv"),
    [
        (
            "skill",
            "disable",
            "notes",
            ["config", "set", "--force", "skills.disabled", '["ascii-art", "notes"]'],
        ),
        ("skill", "enable", "ascii-art", ["config", "set", "--force", "skills.disabled", "[]"]),
        ("mcp", "disable", "jarvis", ["config", "set", "mcp_servers.jarvis.enabled", "false"]),
        ("plugin", "enable", "kanban", ["plugins", "enable", "--no-allow-tool-override", "kanban"]),
        ("plugin", "disable", "kanban", ["plugins", "disable", "kanban"]),
        ("toolset", "disable", "web", ["tools", "disable", "--platform", "api_server", "web"]),
        ("mcp_catalog", "install", "deepwiki", ["mcp", "install", "deepwiki"]),
        (
            "skill_catalog",
            "install",
            "official/finance/ledger",
            ["skills", "install", "official/finance/ledger", "--yes"],
        ),
    ],
)
def test_each_action_is_one_hermes_command(
    home: Path, install: Path, kind: str, op: str, name: str, argv: list[str]
) -> None:
    run = Recorder()
    result = _run(home, install, kind, op, name, run)
    assert run.calls == [argv]
    assert result["ok"] is True and result["message"] == "✓ done"
    assert result["restart_needed"] is (kind == "plugin")


@pytest.mark.parametrize(
    ("kind", "op", "name"),
    [
        ("skill_catalog", "install", "https://evil.example/SKILL.md"),
        ("skill_catalog", "install", "official/../../etc"),
        ("mcp_catalog", "install", "not-in-catalog"),
        ("plugin", "enable", "unknown-plugin"),
        ("skill", "delete", "notes"),
        ("mcp", "enable", "jarvis.url"),
        ("toolset", "enable", "-rf"),
    ],
)
def test_only_listed_items_and_known_actions_are_accepted(
    home: Path, install: Path, kind: str, op: str, name: str
) -> None:
    run = Recorder()
    with pytest.raises(act.ActionRefused):
        _run(home, install, kind, op, name, run)
    assert run.calls == []


def test_a_failed_command_is_reported_with_its_output_and_no_secret(
    home: Path, install: Path
) -> None:
    run = Recorder(
        code=1, output="\x1b[31mError: bad key sk-abcdefghijklmnopqrstuvwxyz0123456789\x1b[0m\n"
    )
    result = _run(home, install, "plugin", "enable", "kanban", run)
    assert result["ok"] is False
    assert result["restart_needed"] is False
    assert "Error: bad key" in result["message"]
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in result["message"]
    assert "\x1b" not in result["message"]


def test_the_catalog_lists_names_descriptions_and_installed_state(
    home: Path, install: Path
) -> None:
    catalog = act.collect_catalog(home, find_install=lambda _h: install)
    assert catalog["available"] is True
    assert catalog["mcp_servers"] == [
        {
            "name": "deepwiki",
            "description": "deepwiki for you.",
            "auth": "none",
            "installed": False,
        },
        {"name": "notion", "description": "notion for you.", "auth": "oauth", "installed": False},
    ]
    assert catalog["skills"] == [
        {
            "id": "official/creative/ascii-art",
            "name": "ascii-art",
            "category": "creative",
            "description": "Draw with text",
            "installed": True,
        },
        {
            "id": "official/finance/ledger",
            "name": "ledger",
            "category": "finance",
            "description": "Books",
            "installed": False,
        },
    ]


def test_routes_refuse_unknown_items_and_run_known_ones(
    home: Path, install: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from jarvis.ui.web import hermes_inventory_routes as routes

    run = Recorder()
    monkeypatch.setattr(routes, "find_hermes_home", lambda: home)
    monkeypatch.setattr(
        routes,
        "run_action",
        lambda kind, op, name, h: act.run_action(
            kind,
            op,
            name,
            h,
            run=run,
            list_plugins=_plugins,
            fetch_toolsets=_toolsets,
            find_install=lambda _h: install,
        ),
    )
    monkeypatch.setattr(
        routes, "collect_catalog", lambda h: act.collect_catalog(h, find_install=lambda _h: install)
    )
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)

    assert len(client.get("/api/hermes/catalog").json()["skills"]) == 2
    ok = client.post(
        "/api/hermes/action", json={"kind": "mcp_catalog", "op": "install", "name": "deepwiki"}
    )
    assert ok.status_code == 200 and ok.json()["ok"] is True
    bad = client.post(
        "/api/hermes/action", json={"kind": "mcp_catalog", "op": "install", "name": "evil"}
    )
    assert bad.status_code == 400
    assert run.calls == [["mcp", "install", "deepwiki"]]
