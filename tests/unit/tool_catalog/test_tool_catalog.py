"""Tool Catalog: one record per upstream, honest states, no secrets.

Fixtures mirror the owner's PC: Jarvis's live mcp.json and Hermes's
config.yaml (``mcp_servers`` + ``platform_toolsets.api_server``).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.tool_catalog import build as build_mod
from jarvis.tool_catalog import sources
from jarvis.tool_catalog.build import build_catalog, filter_items, type_counts
from jarvis.tool_catalog.upstreams import mcp_key, normalize_package, package_from_argv
from jarvis.tool_catalog.verifications import load_verifications

SECRET_ENV = "pcp_board_SUPERSECRET123"  # noqa: S105 — a fake value the test hunts for
SECRET_HEADER = "Bearer jarvis-control-SECRET"  # noqa: S105 — fake, as above

API_SERVER_TOOLSETS = [
    "browser",
    "code_execution",
    "computer_use",
    "connections",
    "cronjob",
    "delegation",
    "file",
    "mcp-jarvis",
    "mcp-windows",
    "memory",
    "session_search",
    "skills",
    "terminal",
    "todo",
    "video",
    "vision",
    "web",
]
BRIDGE_INCLUDE = ["wiki-recall", "wiki-list", "memory_chats", "gmail", "google_calendar"]


def hermes_config() -> dict[str, Any]:
    return {
        "mcp_servers": {
            "windows": {"command": "uvx", "args": ["windows-mcp", "serve"], "enabled": True},
            "playwright": {
                "command": "npx",
                "args": ["-y", "@playwright/mcp@latest", "--browser", "chrome"],
            },
            "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]},
            "microsoft-learn": {"url": "https://learn.microsoft.com/api/mcp"},
            "deepwiki": {"url": "https://mcp.deepwiki.com/mcp"},
            "context7": {"url": "https://mcp.context7.com/mcp?token=QUERYSECRET"},
            "markitdown": {"command": "uvx", "args": ["markitdown-mcp"]},
            "strata": {
                "command": "python.exe",
                "args": [r"C:\Users\Roberto\private\strata\strata_mcp.py"],
                "tools": {"include": ["a", "b", "c", "d"]},
            },
            "paperclip": {
                "command": "npx",
                "args": ["-y", "@paperclipai/mcp-server"],
                "enabled": False,
                "env": {
                    "PAPERCLIP_API_KEY": SECRET_ENV,
                    "PAPERCLIP_API_URL": "http://127.0.0.1:3100",
                },
            },
            "jarvis": {
                "url": "http://127.0.0.1:47821/api/control/mcp/",
                "headers": {"Authorization": SECRET_HEADER},
                "tools": {"include": BRIDGE_INCLUDE},
            },
            "firecrawl-mcp": {"command": "npx", "args": ["firecrawl-mcp"]},
            "apify": {"command": "npx", "args": ["@apify/actors-mcp-server"], "enabled": False},
            "telegram": [],
        },
        "platform_toolsets": {"api_server": list(API_SERVER_TOOLSETS)},
    }


def hermes_inventory() -> dict[str, Any]:
    return {
        "available": True,
        "skills": {
            "items": [
                {"name": "playwright-cli", "provenance": "local", "enabled": True},
                {"name": "notes", "provenance": "bundled", "enabled": False},
            ]
        },
        "plugins": {
            "available": True,
            "items": [
                {"name": "meet", "status": "enabled", "version": "1.0", "description": "Meetings"},
            ],
        },
        "toolsets": {
            "available": True,
            "items": [
                {
                    "name": "web",
                    "label": "Web",
                    "enabled": True,
                    "configured": True,
                    "tool_count": 2,
                },
                {
                    "name": "homeassistant",
                    "label": "Home Assistant",
                    "enabled": False,
                    "configured": False,
                    "tool_count": 4,
                },
                {
                    "name": "mcp-windows",
                    "label": "windows",
                    "enabled": True,
                    "configured": True,
                    "tool_count": 18,
                },
            ],
        },
        "catalog": {"available": True, "mcp_names": ["windows", "github", "linear"]},
    }


def jarvis_mcp(running: bool = True) -> list[dict[str, Any]]:
    return [
        {
            "name": "windows-mcp",
            "command": "uvx.exe",
            "args": ["windows-mcp", "serve"],
            "enabled": True,
            "running": running,
            "tool_count": 18 if running else None,
            "description": "Windows-MCP 0.7.1 (CursorTouch, MIT)",
        },
        {
            "name": "playwright",
            "command": "npx.cmd",
            "args": ["-y", "@playwright/mcp@latest", "--browser", "chrome"],
            "enabled": True,
            "running": False,
            "tool_count": None,
            "error": "RuntimeError: api_key=abcdef0123456789abcdef0123456789 refused",
        },
        {
            "name": "fetch",
            "command": "uvx.exe",
            "args": ["mcp-server-fetch"],
            "enabled": False,
            "running": False,
            "tool_count": None,
        },
    ]


JARVIS_SKILLS = [
    {
        "name": "playwright-cli",
        "state": "active",
        "description": "Drive a browser",
        "is_builtin": False,
    },
    {"name": "plugin-gmail", "state": "active", "description": "Use Gmail", "is_builtin": True},
    {"name": "memory-save", "state": "disabled", "description": "Save", "is_builtin": True},
]
JARVIS_APPS = [
    {
        "id": "gmail",
        "display_name": "Gmail",
        "description": "Mail",
        "status": "connected",
        "auth_mode": "oauth_pkce_loopback",
        "mcp_server": None,
        "native_tool": "gmail",
    },
    {
        "id": "paperclip",
        "display_name": "Paperclip",
        "description": "Agents",
        "status": "not_connected",
        "auth_mode": "pat_paste",
        "mcp_server": {"install": ["npx", "-y", "@paperclipai/mcp-server"], "url": None},
    },
    {
        "id": "spotify",
        "display_name": "Spotify",
        "description": "Music",
        "status": "not_connected",
        "auth_mode": "oauth_pkce_loopback",
        "mcp_server": None,
        "native_tool": "spotify",
    },
]
JARVIS_CLIS = [
    {
        "name": "playwright-cli",
        "display_name": "Playwright CLI",
        "description": "0.1.22",
        "homepage": "",
        "installed": True,
        "connected": True,
        "status": "connected",
        "auth_type": "none",
        "is_custom": True,
    },
    {
        "name": "gh",
        "display_name": "GitHub CLI",
        "description": "GitHub",
        "homepage": "https://cli.github.com",
        "installed": False,
        "connected": False,
        "status": "not_installed",
        "auth_type": "oauth",
        "is_custom": False,
    },
]
BRAIN_TOOLS = [
    "windows-mcp/Snapshot",
    "windows-mcp/Click",
    "gmail",
    "cli_playwright-cli",
    "wiki-recall",
]


def catalog(active: str | None = "hermes", **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "active_brain": active,
        "jarvis_mcp": jarvis_mcp(),
        "jarvis_skills": JARVIS_SKILLS,
        "jarvis_apps": JARVIS_APPS,
        "jarvis_clis": JARVIS_CLIS,
        "brain_tools": BRAIN_TOOLS,
        "hermes_config": hermes_config(),
        "hermes_inventory": hermes_inventory(),
        "verifications": {},
    }
    kwargs.update(overrides)
    return build_catalog(**kwargs)


def by_id(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ids = [r["id"] for r in result["items"]]
    assert len(ids) == len(set(ids)), "a canonical id appears twice"
    return {r["id"]: r for r in result["items"]}


def binding(record: dict[str, Any], runtime: str, kind: str = "mcp") -> dict[str, Any]:
    hits = [b for b in record["bindings"] if b["runtime"] == runtime and b["kind"] == kind]
    assert len(hits) == 1, hits
    return hits[0]


# ---------------------------------------------------------------------------
# Canonical keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "package"),
    [
        (["uvx", "windows-mcp", "serve"], "windows-mcp"),
        (["uvx.exe", "windows-mcp", "serve"], "windows-mcp"),
        ([r"C:\Users\R\AppData\Local\Programs\windows-mcp\windows-mcp.exe"], "windows-mcp"),
        (["npx.cmd", "-y", "@playwright/mcp@latest", "--browser", "chrome"], "@playwright/mcp"),
        (["cmd", "/c", "npx", "-y", "@playwright/mcp@0.0.41"], "@playwright/mcp"),
        (
            ["uv", "tool", "run", "--from", "markitdown-mcp==0.1", "markitdown-mcp"],
            "markitdown-mcp",
        ),
        (["python", "-m", "some_server"], "some_server"),
        (["python.exe", r"C:\x\strata_mcp.py"], "strata_mcp"),
    ],
)
def test_package_from_argv(argv: list[str], package: str) -> None:
    assert package_from_argv(argv) == package


def test_keys_never_carry_paths_or_queries() -> None:
    assert (
        mcp_key(name="strata", command="python.exe", args=[r"C:\Users\Roberto\x\strata_mcp.py"])
        == "strata"
    )
    assert mcp_key(name="x", url="https://mcp.example.com/mcp?key=SECRET") == "url:mcp.example.com"
    assert mcp_key(name="jarvis", url="http://127.0.0.1:47821/api/control/mcp/") == "jarvis-bridge"
    assert mcp_key(name="telegram") is None
    assert normalize_package("@Scope/Pkg@1.2.3") == "@scope/pkg"


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------


def test_same_upstream_in_both_runtimes_is_one_record() -> None:
    items = by_id(catalog())
    windows = items["windows-mcp"]
    assert {(b["runtime"], b["local_id"]) for b in windows["bindings"]} == {
        ("jarvis", "windows-mcp"),
        ("hermes", "windows"),
    }
    assert windows["name"] == "Windows-MCP"
    assert windows["license"] == "MIT"
    assert windows["upstream_url"] == "https://github.com/CursorTouch/Windows-MCP"
    assert windows["role"] == "canonical"
    for key, hermes_name in (("@playwright/mcp", "playwright"), ("mcp-server-fetch", "fetch")):
        assert {(b["runtime"], b["local_id"]) for b in items[key]["bindings"]} == {
            ("jarvis", hermes_name),
            ("hermes", hermes_name),
        }
    assert items["@playwright/mcp"]["duplicate_of"] == "playwright-cli"
    assert items["@playwright/mcp"]["license"] == "Apache-2.0"


def test_cursortouch_exe_still_dedupes_with_hermes_windows() -> None:
    mcp = jarvis_mcp()
    mcp[0] = {**mcp[0], "command": r"C:\Users\R\.local\bin\windows-mcp.exe", "args": []}
    items = by_id(catalog(jarvis_mcp=mcp))
    assert len(items["windows-mcp"]["bindings"]) == 2


def test_known_and_unknown_upstreams_and_the_hosted_ones() -> None:
    items = by_id(catalog())
    assert items["context7"]["license"] == "MIT"
    assert items["deepwiki"]["notes"].startswith("Free hosted service by Cognition")
    assert items["microsoft-learn"]["upstream_url"] == "https://github.com/MicrosoftDocs/mcp"
    assert items["markitdown-mcp"]["license"] == "MIT"
    assert items["strata"]["source"] == "local"
    assert items["strata"]["upstream_url"] is None
    assert binding(items["strata"], "hermes")["tool_filter"] == {"include": 4, "exclude": None}
    assert items["jarvis-bridge"]["needs_credentials"] is True
    assert items["firecrawl-mcp"]["needs_credentials"] is True
    assert items["@apify/actors-mcp-server"]["states"]["enabled"] is False
    # a Hermes catalog entry that is not installed: available, nothing more
    github = items["mcp:github"]
    assert github["states"]["available"] is True
    assert github["states"]["installed"] is False
    assert github["source"] == "hermes"
    # the catalog's "windows" is already configured: no second record
    assert not any(b.get("catalog") for b in items["windows-mcp"]["bindings"])


def test_malformed_hermes_entry_is_shown_as_broken() -> None:
    telegram = by_id(catalog())["hermes-mcp:telegram"]
    assert telegram["invalid"] is True
    assert telegram["types"] == ["mcp"]
    assert telegram["states"]["available"] is False
    assert binding(telegram, "hermes")["problems"] == ["malformed config"]
    assert telegram["source"] == "unknown"


def test_apps_cli_and_skills_fold_into_their_upstream() -> None:
    items = by_id(catalog())
    paperclip = items["@paperclipai/mcp-server"]
    assert paperclip["types"] == ["mcp", "app"]
    assert {b["runtime"] for b in paperclip["bindings"]} == {"jarvis", "hermes"}
    assert paperclip["needs_credentials"] is True
    gmail = items["app:gmail"]
    assert gmail["types"] == ["app", "skill"]
    assert "skill:plugin-gmail" not in items
    pw = items["playwright-cli"]
    assert pw["types"] == ["cli", "skill"]
    assert {(b["runtime"], b["kind"]) for b in pw["bindings"]} == {
        ("jarvis", "cli"),
        ("jarvis", "skill"),
        ("hermes", "skill"),
    }
    assert pw["role"] == "primary"
    assert items["app:spotify"]["states"]["connected"] is False


def test_toolsets_and_plugins() -> None:
    items = by_id(catalog())
    web = items["toolset:web"]
    assert web["types"] == ["toolset"]
    assert web["source"] == "hermes"
    assert web["tool_count"] == 2
    assert web["states"]["visible"] is True
    assert "connected" in web["not_applicable"]
    assert items["toolset:homeassistant"]["states"]["visible"] is False
    assert "toolset:mcp-windows" not in items  # folded into the server
    assert binding(items["windows-mcp"], "hermes")["tool_count"] == 18
    assert items["plugin:meet"]["states"]["visible"] is None


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------


def test_visibility_when_hermes_is_the_brain() -> None:
    items = by_id(catalog("hermes"))
    windows = items["windows-mcp"]
    assert binding(windows, "hermes")["visible_to_active_brain"] is True  # mcp-windows listed
    # Jarvis's windows-mcp tools are not in the bridge's include list
    assert binding(windows, "jarvis")["visible_to_active_brain"] is False
    assert binding(items["@playwright/mcp"], "hermes")["visible_to_active_brain"] is False
    assert binding(items["jarvis-bridge"], "hermes")["visible_to_active_brain"] is True
    assert binding(items["app:gmail"], "jarvis", "app")["visible_to_active_brain"] is True
    assert binding(items["playwright-cli"], "hermes", "skill")["visible_to_active_brain"] is True
    assert binding(items["playwright-cli"], "jarvis", "cli")["visible_to_active_brain"] is False
    assert items["skill:notes"]["states"]["visible"] is False  # disabled


def test_visibility_when_jarvis_is_the_brain() -> None:
    items = by_id(catalog("jarvis"))
    windows = items["windows-mcp"]
    assert binding(windows, "jarvis")["visible_to_active_brain"] is True
    assert binding(windows, "hermes")["visible_to_active_brain"] is False
    assert binding(items["@playwright/mcp"], "jarvis")["visible_to_active_brain"] is False
    assert binding(items["playwright-cli"], "jarvis", "cli")["visible_to_active_brain"] is True


def test_unknowns_stay_unknown() -> None:
    items = by_id(catalog(None, brain_tools=None))
    for record in items.values():
        assert record["states"]["visible"] in (None, False)
        for b in record["bindings"]:
            if b["enabled"] is False or b["connected"] is False or b["problems"]:
                assert b["visible_to_active_brain"] is False  # off is never visible
            else:
                assert b["visible_to_active_brain"] is None  # nothing guessed true
    items = by_id(catalog("jarvis", brain_tools=None))
    assert binding(items["windows-mcp"], "jarvis")["visible_to_active_brain"] is None
    # Hermes gives no live MCP status unless its toolset reported tools
    assert binding(items["@playwright/mcp"], "hermes")["connected"] is None
    assert binding(items["windows-mcp"], "hermes")["connected"] is True
    no_platform = hermes_config()
    del no_platform["platform_toolsets"]
    items = by_id(catalog("hermes", hermes_config=no_platform))
    assert binding(items["windows-mcp"], "hermes")["visible_to_active_brain"] is None


def test_connected_is_never_verified_without_a_real_call() -> None:
    windows = by_id(catalog())["windows-mcp"]
    assert windows["states"]["connected"] is True
    assert windows["states"]["enabled"] is True
    assert windows["states"]["verified"] is None
    assert windows["verification"] is None


def test_verification_entries_drive_verified() -> None:
    entries = {
        "jarvis:windows-mcp": {
            "checked_at": "2026-10-12T09:00:00Z",
            "ok": True,
            "action": "Snapshot",
            "latency_ms": 840,
            "tool_count": 18,
            "error": None,
        },
        "hermes:context7": {
            "checked_at": "2026-10-12T09:05:00Z",
            "ok": False,
            "action": "resolve",
            "latency_ms": 3000,
            "tool_count": None,
            "error": "timeout",
        },
        "skill:playwright-cli": {
            "checked_at": "2026-10-11T08:00:00Z",
            "ok": True,
            "action": "open example.com",
            "latency_ms": 2100,
            "tool_count": None,
            "error": None,
        },
    }
    items = by_id(catalog(verifications=entries))
    assert items["windows-mcp"]["states"]["verified"] is True
    assert binding(items["windows-mcp"], "jarvis")["verification"]["latency_ms"] == 840
    assert binding(items["windows-mcp"], "hermes")["verification"] is None
    assert items["context7"]["states"]["verified"] is False
    assert items["context7"]["verification"]["error"] == "timeout"
    assert items["playwright-cli"]["states"]["verified"] is True
    assert items["mcp-server-fetch"]["states"]["verified"] is None


def test_verification_file_is_read_safely(tmp_path: Path) -> None:
    missing, status = load_verifications(tmp_path / "nope.json")
    assert missing == {} and status["found"] is False
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    entries, status = load_verifications(bad)
    assert entries == {} and status["error"]
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"version": 2, "items": {}}), encoding="utf-8")
    assert load_verifications(wrong)[0] == {}
    good = tmp_path / "good.json"
    good.write_text(
        json.dumps(
            {
                "version": 1,
                "items": {
                    "Jarvis:Windows-MCP": {
                        "checked_at": "2026-10-12T09:00:00Z",
                        "ok": True,
                        "latency_ms": 5,
                    },
                    "hermes:x": {"ok": "yes"},
                    "hermes:y": "nope",
                },
            }
        ),
        encoding="utf-8",
    )
    entries, status = load_verifications(good)
    assert list(entries) == ["jarvis:windows-mcp"]
    assert entries["jarvis:windows-mcp"]["tool_count"] is None
    assert status == {"found": True, "loaded": True, "entries": 1, "error": None}


def test_no_secret_reaches_the_catalog() -> None:
    text = json.dumps(catalog())
    for secret in (
        SECRET_ENV,
        SECRET_HEADER,
        "QUERYSECRET",
        "abcdef0123456789abcdef0123456789",
        "Roberto",
        "strata_mcp.py",
        "127.0.0.1:3100",
    ):
        assert secret not in text
    paperclip = by_id(catalog())["@paperclipai/mcp-server"]
    assert binding(paperclip, "hermes")["credential_names"] == [
        "PAPERCLIP_API_KEY",
        "PAPERCLIP_API_URL",
    ]


def test_search_and_type_filters() -> None:
    items = catalog()["items"]
    hits = filter_items(items, q="windows")
    assert [r["id"] for r in hits] == ["windows-mcp"]
    assert {r["id"] for r in filter_items(items, q="apache browser")} == {
        "@playwright/mcp",
        "playwright-cli",
    }
    assert [r["id"] for r in filter_items(items, q="secondary browser")] == ["@playwright/mcp"]
    skills = filter_items(items, type_="skill")
    assert {"playwright-cli", "app:gmail", "skill:notes"} <= {r["id"] for r in skills}
    counts = type_counts(filter_items(items, q="playwright"))
    assert counts["cli"] == 1 and counts["mcp"] == 1 and counts["skill"] == 1


def test_hermes_missing_keeps_jarvis_records() -> None:
    items = by_id(catalog(hermes_config=None, hermes_inventory=None))
    assert {b["runtime"] for b in items["windows-mcp"]["bindings"]} == {"jarvis"}
    assert not any(r["id"].startswith(("toolset:", "hermes-mcp:")) for r in items.values())


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


def test_route_serves_filtered_catalog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from jarvis.ui.web import tool_catalog_routes

    monkeypatch.setattr(sources, "jarvis_mcp_servers", lambda _state: jarvis_mcp())
    monkeypatch.setattr(sources, "jarvis_apps", lambda: JARVIS_APPS)
    monkeypatch.setattr(
        sources,
        "hermes_sources",
        lambda: (hermes_config(), hermes_inventory(), {"ok": True, "error": None}),
    )
    monkeypatch.setattr(sources, "verifications_path", lambda: tmp_path / "v.json")
    monkeypatch.setattr(
        sources, "load_verifications", lambda: load_verifications(tmp_path / "v.json")
    )

    app = FastAPI()
    app.include_router(tool_catalog_routes.router)
    app.state.config = SimpleNamespace(brain=SimpleNamespace(primary="hermes"))
    app.state.brain = SimpleNamespace(_tools=dict.fromkeys(BRAIN_TOOLS))
    app.state.skill_registry = None
    app.state.cli_registry = None
    with TestClient(app) as client:
        body = client.get("/api/tool-catalog", params={"q": "playwright", "type": "mcp"}).json()
        full = client.get("/api/tool-catalog").json()

    assert body["active_brain"] == "hermes"
    assert [r["id"] for r in body["items"]] == ["@playwright/mcp"]
    assert body["counts"]["mcp"] == 1
    assert body["sources"]["jarvis_skills"]["ok"] is False
    assert body["sources"]["verifications"]["found"] is False
    assert full["total"] == full["total_unfiltered"]
    assert SECRET_ENV not in json.dumps(full)
    assert build_mod.CATALOG_VERSION == full["version"]
