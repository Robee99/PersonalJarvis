"""Gather the catalog's inputs from the running app and from Hermes's own files.

Reuses the systems that already own each list: ``jarvis.mcp.state`` and the MCP
registry (``app.state.mcp_registry``), the skill registry, the Marketplace
catalog and token store, the CLI registry, the brain's tool registry
(``app.state.brain._tools``) and :mod:`jarvis.plugins.brain.hermes_inventory`.

Each source fails on its own and reports why in ``sources``; the catalog then
simply lacks that source's bindings. Blocking IO (files, keyring, a ``hermes``
subprocess, the loopback Hermes API): call from a worker thread.
"""

from __future__ import annotations

import logging
from typing import Any

from .build import build_catalog
from .verifications import load_verifications, verifications_path

log = logging.getLogger(__name__)

Json = dict[str, Any]


def _ok() -> Json:
    return {"ok": True, "error": None}


def _failed(source: str, exc: BaseException) -> Json:
    log.warning("tool catalog: %s unavailable: %s", source, type(exc).__name__)
    return {"ok": False, "error": f"{source} could not be read"}


def active_brain(app_state: Any) -> str | None:
    """``hermes`` or ``jarvis`` from ``brain.primary``; ``None`` without a config."""
    cfg = getattr(app_state, "config", None)
    if cfg is None or getattr(cfg, "brain", None) is None:
        return None
    from jarvis.brain.route_policy import hermes_is_main_brain

    return "hermes" if hermes_is_main_brain(cfg) else "jarvis"


def brain_tool_names(app_state: Any) -> list[str] | None:
    """Names in the live BrainManager registry, or ``None`` before the brain is built."""
    registry = getattr(getattr(app_state, "brain", None), "_tools", None)
    if registry is None or not hasattr(registry, "keys"):
        return None
    return [str(name) for name in list(registry.keys())]


def jarvis_mcp_servers(app_state: Any) -> list[Json]:
    """mcp.json entries (resolved by ``jarvis.mcp.state``) plus registry-only specs."""
    from jarvis.mcp import state as mcp_state

    registry = getattr(app_state, "mcp_registry", None)
    active = registry.active_clients() if registry is not None else None
    entries = mcp_state.load_config().get("mcpServers", {})
    rows: dict[str, Json] = {}
    for name, entry in entries.items() if isinstance(entries, dict) else []:
        if not isinstance(entry, dict):
            continue
        env = entry.get("env") if isinstance(entry.get("env"), dict) else {}
        rows[str(name)] = {
            "name": str(name),
            "command": entry.get("command"),
            "args": entry.get("args") if isinstance(entry.get("args"), list) else [],
            "url": entry.get("url"),
            "transport": entry.get("transport") or ("http" if entry.get("url") else "stdio"),
            "enabled": bool(entry.get("enabled", False)),
            "display": entry.get("display"),
            "description": entry.get("description"),
            "required_auth": [str(k) for k in entry.get("required_auth") or []],
            "env_keys": [str(k) for k in env],
        }
    if registry is not None:
        for spec in registry.all_specs():
            row = rows.setdefault(
                spec.name,
                {
                    "name": spec.name,
                    "command": spec.install_command[0] if spec.install_command else None,
                    "args": list(spec.install_command[1:]),
                    "url": spec.url,
                    "transport": spec.transport,
                    "enabled": False,
                    "display": spec.display,
                    "description": spec.description,
                    "required_auth": list(spec.required_auth),
                    "env_keys": [],
                },
            )
            row.setdefault("display", spec.display)
    for name, row in rows.items():
        if active is None:
            row["running"] = None
            row["tool_count"] = None
            row["error"] = None
            continue
        client = active.get(name)
        row["running"] = client is not None and bool(getattr(client, "is_healthy", True))
        cache = getattr(client, "_tools_cache", None) if client is not None else None
        row["tool_count"] = len(cache) if isinstance(cache, list) else None
        row["error"] = registry.last_error(name)
    return list(rows.values())


def jarvis_skills(app_state: Any) -> list[Json] | None:
    registry = getattr(app_state, "skill_registry", None)
    if registry is None:
        return None
    from jarvis.skills.builtin import BUILTIN_SKILL_NAMES

    out = []
    for skill in registry.list():
        fm = skill.frontmatter
        out.append(
            {
                "name": skill.name,
                "state": getattr(skill.state, "value", str(skill.state)),
                "description": fm.description if fm else "",
                "is_builtin": skill.name in BUILTIN_SKILL_NAMES,
            }
        )
    return out


def jarvis_apps() -> list[Json]:
    """Marketplace connectors with their stored connection status (never a token)."""
    from jarvis.marketplace.catalog_data import load_catalog
    from jarvis.marketplace.token_store import TokenStore
    from jarvis.ui.web.marketplace_routes import _plugin_status

    store = TokenStore()
    out = []
    for spec in load_catalog().plugins:
        auth = getattr(spec, "auth", None)
        mcp = spec.mcp_server if isinstance(spec.mcp_server, dict) else None
        out.append(
            {
                "id": spec.id,
                "display_name": spec.display_name,
                "description": spec.description,
                "status": _plugin_status(spec.id, store),
                "auth_mode": str(getattr(auth, "mode", "") or ""),
                # Only what identifies the upstream: the launch argv or the URL. The
                # env/header templates stay behind.
                "mcp_server": {"install": mcp.get("install"), "url": mcp.get("url")}
                if mcp
                else None,
                "native_tool": getattr(spec, "native_tool", None),
            }
        )
    return out


def jarvis_clis(app_state: Any) -> list[Json] | None:
    registry = getattr(app_state, "cli_registry", None)
    if registry is None:
        return None
    from jarvis.ui.web.cli_routes import _status_string

    statuses = registry.all_status()
    out = []
    for name, spec in registry.catalog().all().items():
        status = statuses.get(name)
        label = _status_string(spec, status)
        out.append(
            {
                "name": name,
                "display_name": spec.display_name,
                "description": spec.description,
                "homepage": spec.homepage,
                "installed": None if status is None else bool(status.installed),
                "connected": None if status is None else label == "connected",
                "status": label,
                "error": status.error if status else None,
                "auth_type": str(spec.auth.type),
                "is_custom": spec.source == "custom",
            }
        )
    return out


def hermes_sources() -> tuple[Json | None, Json | None, Json]:
    """``(config.yaml, inventory, status)``; both ``None`` when Hermes is not installed."""
    from jarvis.plugins.brain.hermes_inventory import (
        NOT_FOUND_REASON,
        _load_config,
        collect_hermes_inventory,
        find_hermes_home,
    )

    home = find_hermes_home()
    if home is None:
        return None, None, {"ok": False, "error": NOT_FOUND_REASON}
    config, config_error = _load_config(home)
    inventory = collect_hermes_inventory()
    status = {"ok": config_error is None, "error": config_error}
    return config, inventory, status


def collect_catalog(app_state: Any) -> Json:
    """The full catalog for the running app; every source degrades on its own."""
    status: dict[str, Json] = {}

    def attempt(name: str, fn: Any, *args: Any) -> Any:
        try:
            value = fn(*args)
        except Exception as exc:  # noqa: BLE001 — one broken source must not hide the others
            status[name] = _failed(name, exc)
            return None
        status[name] = _ok() if value is not None else {"ok": False, "error": f"{name} not ready"}
        return value

    brain = attempt("active_brain", active_brain, app_state)
    tools = attempt("brain_tools", brain_tool_names, app_state)
    mcp = attempt("jarvis_mcp", jarvis_mcp_servers, app_state)
    skills = attempt("jarvis_skills", jarvis_skills, app_state)
    apps = attempt("jarvis_apps", jarvis_apps)
    clis = attempt("jarvis_clis", jarvis_clis, app_state)
    try:
        hermes_config, hermes_inventory, hermes_status = hermes_sources()
    except Exception as exc:  # noqa: BLE001 — Hermes being broken must not hide Jarvis's tools
        hermes_config, hermes_inventory, hermes_status = None, None, _failed("hermes", exc)
    status["hermes"] = hermes_status
    entries, file_status = load_verifications()
    status["verifications"] = {
        "ok": file_status["loaded"],
        "error": file_status["error"],
        "found": file_status["found"],
        "entries": file_status["entries"],
        "path": str(verifications_path()),
    }
    return build_catalog(
        active_brain=brain,
        jarvis_mcp=mcp,
        jarvis_skills=skills,
        jarvis_apps=apps,
        jarvis_clis=clis,
        brain_tools=tools,
        hermes_config=hermes_config,
        hermes_inventory=hermes_inventory,
        verifications=entries,
        source_status=status,
    )
