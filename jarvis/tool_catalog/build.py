"""Fold Jarvis's and Hermes's lists into one record per canonical upstream.

Pure: every input is plain data gathered elsewhere (:mod:`.sources`), so the
whole dedupe and state logic is testable without a running app. ``None`` for a
source means it could not be read; its records are then missing, never faked.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .upstreams import (
    HERMES_TOOLSETS,
    HERMES_UPSTREAM,
    mcp_key,
    name_key,
    tool_name_key,
    upstream_for,
)

CATALOG_VERSION = 1
TYPE_ORDER = ("mcp", "app", "cli", "skill", "plugin", "toolset")
STATE_NAMES = ("available", "installed", "enabled", "connected", "visible", "verified")
#: States that mean nothing for a binding kind (a skill is never "connected").
_NOT_APPLICABLE = {
    "cli": frozenset(("enabled",)),
    "skill": frozenset(("connected",)),
    "plugin": frozenset(("connected",)),
    "toolset": frozenset(("connected",)),
}
_DESCRIPTION_CAP = 240
_ERROR_CAP = 200

Json = dict[str, Any]


def _tri(values: Iterable[bool | None]) -> bool | None:
    """Any True wins; all False is False; anything unknown and no True is None."""
    seen = list(values)
    if any(v is True for v in seen):
        return True
    if seen and all(v is False for v in seen):
        return False
    return None


def _boolish(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, (bool, int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    return default


def _names(raw: Any) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return None
    return [str(item).strip() for item in raw if str(item).strip()]


def _short(text: Any, cap: int = _DESCRIPTION_CAP) -> str | None:
    if not isinstance(text, str):
        return None
    clean = " ".join(text.split())
    return clean[:cap] if clean else None


def _safe_error(text: Any) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None
    from jarvis.core.redact import redact_secrets

    return redact_secrets(" ".join(text.split()))[:_ERROR_CAP]


def _wire_prefix(prefix: str) -> str:
    """What a ``server/`` tool prefix becomes on the Jarvis MCP bridge (``server_``)."""
    return prefix.replace("/", "_")


# ---------------------------------------------------------------------------
# Visibility to the active brain
# ---------------------------------------------------------------------------


@dataclass
class _Bridge:
    name: str
    enabled: bool
    listed: bool | None
    include: list[str] | None


class Visibility:
    """Whether the ACTIVE brain can see a binding's tools; ``None`` when unknowable."""

    def __init__(
        self,
        active_brain: str | None,
        brain_tools: Iterable[str] | None,
        hermes_config: Json | None,
    ) -> None:
        self.active = active_brain
        self.brain_tools = set(brain_tools) if brain_tools is not None else None
        self.hermes_known = hermes_config is not None
        platform = (hermes_config or {}).get("platform_toolsets")
        raw = platform.get("api_server") if isinstance(platform, dict) else None
        listed = _names(raw)
        self.api_toolsets = set(listed) if listed is not None else None
        self.bridge = self._find_bridge(hermes_config or {})

    def _find_bridge(self, config: Json) -> _Bridge | None:
        servers = config.get("mcp_servers")
        if not isinstance(servers, dict):
            return None
        for name, entry in servers.items():
            if not isinstance(entry, dict):
                continue
            key = mcp_key(
                name=str(name),
                command=entry.get("command"),
                args=entry.get("args"),
                url=entry.get("url"),
            )
            if key != "jarvis-bridge":
                continue
            tools = entry.get("tools") if isinstance(entry.get("tools"), dict) else {}
            return _Bridge(
                name=str(name),
                enabled=_boolish(entry.get("enabled", True)),
                listed=(f"mcp-{name}" in self.api_toolsets)
                if self.api_toolsets is not None
                else None,
                include=_names(tools.get("include")),
            )
        return None

    def hermes(self, *, enabled: bool | None, toolsets: Iterable[str]) -> bool | None:
        """A Hermes binding: enabled AND one of ``toolsets`` is on Hermes's api_server platform."""
        if enabled is False:
            return False
        if self.active is None:
            return None
        if self.active != "hermes":
            return False
        if enabled is None or self.api_toolsets is None:
            return None
        return any(name in self.api_toolsets for name in toolsets)

    def jarvis(
        self,
        *,
        enabled: bool | None = True,
        exact: Iterable[str] = (),
        prefixes: Iterable[str] = (),
    ) -> bool | None:
        """A Jarvis binding: in the brain's registry, or reachable over Hermes's Jarvis bridge."""
        exact, prefixes = set(exact), tuple(prefixes)
        if enabled is False:
            return False
        if self.active is None:
            return None

        def in_registry() -> bool | None:
            if self.brain_tools is None:
                return None
            return any(t in exact or t.startswith(prefixes) for t in self.brain_tools)

        if self.active == "jarvis":
            return in_registry()
        # Hermes is the brain: Jarvis's tools reach it only through mcp_servers.jarvis.
        if not self.hermes_known:
            return None
        bridge = self.bridge
        if bridge is None or not bridge.enabled or bridge.listed is False:
            return False
        if bridge.listed is None:
            return None
        if bridge.include is None:
            return in_registry()
        wire = tuple(_wire_prefix(p) for p in prefixes)
        return any(name in exact or name.startswith(wire) for name in bridge.include)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class _Group:
    key: str
    types: set[str] = field(default_factory=set)
    bindings: list[Json] = field(default_factory=list)
    names: list[tuple[int, str]] = field(default_factory=list)
    descriptions: list[str] = field(default_factory=list)
    source: str | None = None
    upstream_url: str | None = None
    license: str | None = None
    needs_credentials: list[bool | None] = field(default_factory=list)


class _Catalog:
    def __init__(self, verifications: dict[str, Json]) -> None:
        self.groups: dict[str, _Group] = {}
        self.verifications = verifications

    def add(
        self,
        key: str,
        *,
        type_: str,
        binding: Json | None,
        name: str,
        name_rank: int,
        description: Any = None,
        source: str | None = None,
        upstream_url: str | None = None,
        needs_credentials: bool | None = None,
    ) -> None:
        group = self.groups.setdefault(key, _Group(key=key))
        group.types.add(type_)
        group.names.append((name_rank, name))
        text = _short(description)
        if text and text not in group.descriptions:
            group.descriptions.append(text)
        if source and group.source is None:
            group.source = source
        if upstream_url and group.upstream_url is None:
            group.upstream_url = upstream_url
        group.needs_credentials.append(needs_credentials)
        if binding is not None:
            binding["verification"] = self._verification_for(binding)
            group.bindings.append(binding)

    def _verification_for(self, binding: Json) -> Json | None:
        local = str(binding["local_id"]).lower()
        for key in (f"{binding['runtime']}:{local}", f"{binding['kind']}:{local}"):
            hit = self.verifications.get(key)
            if hit is not None:
                return {"key": key, **hit}
        return None


def _binding(
    runtime: str,
    kind: str,
    local_id: str,
    *,
    installed: bool | None,
    enabled: bool | None,
    connected: bool | None,
    tool_count: int | None,
    visible: bool | None,
    **extra: Any,
) -> Json:
    out: Json = {
        "runtime": runtime,
        "kind": kind,
        "local_id": local_id,
        "installed": installed,
        "enabled": enabled,
        "connected": connected,
        "tool_count": tool_count,
        "visible_to_active_brain": visible,
        "problems": [],
        "detail": None,
    }
    out.update(extra)
    return out


def _finish(group: _Group) -> Json:
    meta = upstream_for(group.key)
    kinds = {b["kind"] for b in group.bindings}
    types = [t for t in TYPE_ORDER if t in group.types]
    not_applicable = [
        state
        for state in ("installed", "enabled", "connected")
        if kinds and all(state in _NOT_APPLICABLE.get(kind, frozenset()) for kind in kinds)
    ]

    def state(name: str) -> bool | None:
        return _tri(
            b[name]
            for b in group.bindings
            if name not in _NOT_APPLICABLE.get(b["kind"], frozenset())
        )

    verifications = [b["verification"] for b in group.bindings if b.get("verification")]
    latest = max(verifications, key=lambda v: v["checked_at"]) if verifications else None
    invalid = bool(group.bindings) and all(b["problems"] for b in group.bindings)
    states = {
        "available": not invalid,
        "installed": None if "installed" in not_applicable else state("installed"),
        "enabled": None if "enabled" in not_applicable else state("enabled"),
        "connected": None if "connected" in not_applicable else state("connected"),
        "visible": _tri(b["visible_to_active_brain"] for b in group.bindings),
        "verified": True
        if any(v["ok"] for v in verifications)
        else (False if verifications else None),
    }
    counts = [b["tool_count"] for b in group.bindings if isinstance(b.get("tool_count"), int)]
    name = meta.name if meta else sorted(group.names, key=lambda n: n[0])[0][1]
    needs = (
        meta.needs_credentials
        if meta and meta.needs_credentials is not None
        else _tri(group.needs_credentials)
    )
    if meta:
        source = meta.source
    else:
        source = group.source or "unknown"
    runtime_order = {"jarvis": 0, "hermes": 1}
    bindings = sorted(group.bindings, key=lambda b: (runtime_order.get(b["runtime"], 2), b["kind"]))
    return {
        "id": group.key,
        "name": name,
        "types": types,
        "description": group.descriptions[0] if group.descriptions else None,
        "source": source,
        "upstream_url": meta.upstream_url if meta and meta.upstream_url else group.upstream_url,
        "license": meta.license if meta else None,
        "needs_credentials": needs,
        "notes": meta.notes if meta else None,
        "role": meta.role if meta else None,
        "duplicate_of": meta.duplicate_of if meta else None,
        "invalid": invalid,
        "states": states,
        "not_applicable": not_applicable,
        "tool_count": max(counts) if counts else None,
        "verification": latest,
        "bindings": bindings,
    }


# ---------------------------------------------------------------------------
# Sources → groups
# ---------------------------------------------------------------------------


def _add_jarvis_mcp(cat: _Catalog, vis: Visibility, servers: list[Json]) -> set[str]:
    keys: set[str] = set()
    for server in servers:
        name = str(server.get("name") or "").strip()
        if not name:
            continue
        key = (
            mcp_key(
                name=name,
                command=server.get("command"),
                args=server.get("args"),
                url=server.get("url"),
            )
            or f"mcp:{name.lower()}"
        )
        keys.add(key)
        enabled = bool(server.get("enabled"))
        running = server.get("running")
        cred_names = sorted({*(server.get("required_auth") or []), *(server.get("env_keys") or [])})
        binding = _binding(
            "jarvis",
            "mcp",
            name,
            installed=True,
            enabled=enabled,
            connected=running if isinstance(running, bool) else None,
            tool_count=server.get("tool_count") if running else None,
            visible=vis.jarvis(
                enabled=False if running is False else enabled, prefixes=[f"{name}/"]
            ),
            transport=server.get("transport"),
            credential_names=cred_names,
        )
        binding["detail"] = _safe_error(server.get("error"))
        cat.add(
            key,
            type_="mcp",
            binding=binding,
            name=str(server.get("display") or name),
            name_rank=5,
            description=server.get("description"),
            needs_credentials=True if cred_names else None,
        )
    return keys


def _hermes_mcp_toolsets(inventory: Json | None) -> dict[str, Json]:
    part = (inventory or {}).get("toolsets") or {}
    items = part.get("items") if isinstance(part, dict) and part.get("available") else None
    return {
        str(item.get("name")): item
        for item in (items or [])
        if isinstance(item, dict) and str(item.get("name") or "").startswith("mcp-")
    }


def _add_hermes_mcp(
    cat: _Catalog, vis: Visibility, config: Json, inventory: Json | None
) -> tuple[set[str], set[str]]:
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        return set(), set()
    reported = _hermes_mcp_toolsets(inventory)
    keys: set[str] = set()
    names: set[str] = set()
    for raw_name, entry in sorted(servers.items(), key=lambda kv: str(kv[0])):
        name = str(raw_name)
        names.add(name.lower())
        cfg = entry if isinstance(entry, dict) else {}
        problems: list[str] = []
        has_url, has_command = bool(cfg.get("url")), bool(cfg.get("command"))
        if not isinstance(entry, dict) or not (has_url or has_command):
            problems.append("malformed config")
        elif has_url and has_command:
            problems.append("malformed config: both a command and a URL")
        key = (
            mcp_key(name=name, command=cfg.get("command"), args=cfg.get("args"), url=cfg.get("url"))
            if not problems
            else None
        ) or f"hermes-mcp:{name.lower()}"
        keys.add(key)
        enabled = _boolish(cfg.get("enabled", True)) if not problems else None
        tools = cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}
        include, exclude = _names(tools.get("include")), _names(tools.get("exclude"))
        toolset = reported.get(f"mcp-{name}")
        count = toolset.get("tool_count") if toolset else None
        count = count if isinstance(count, int) else None
        visible = False if problems else vis.hermes(enabled=enabled, toolsets=[f"mcp-{name}"])
        if visible and include is not None and not include:
            visible = False  # an empty include list lets no tool through
        env = cfg.get("env") if isinstance(cfg.get("env"), dict) else {}
        headers = cfg.get("headers") if isinstance(cfg.get("headers"), dict) else {}
        cred_names = sorted({str(k) for k in (*env.keys(), *headers.keys())})
        binding = _binding(
            "hermes",
            "mcp",
            name,
            installed=True,
            enabled=enabled,
            # Hermes reports no live MCP status; tools it discovered for the
            # server's toolset are the only connection evidence it gives.
            connected=True if count else None,
            tool_count=count,
            visible=visible,
            transport="http" if has_url and not has_command else "stdio" if has_command else None,
            tool_filter={
                "include": len(include) if include is not None else None,
                "exclude": len(exclude) if exclude is not None else None,
            },
            credential_names=cred_names,
        )
        binding["problems"] = problems
        cat.add(
            key,
            type_="mcp",
            binding=binding,
            name=name,
            name_rank=6,
            needs_credentials=True if cred_names else None,
        )
    return keys, names


def _add_hermes_mcp_catalog(
    cat: _Catalog, inventory: Json | None, configured_keys: set[str], configured_names: set[str]
) -> None:
    part = (inventory or {}).get("catalog") or {}
    if not isinstance(part, dict) or not part.get("available"):
        return
    for raw in part.get("mcp_names") or []:
        name = str(raw).strip()
        if not name or name.lower() in configured_names:
            continue
        key = name_key(name) or f"mcp:{name.lower()}"
        if key in configured_keys:
            continue
        binding = _binding(
            "hermes",
            "mcp",
            name,
            installed=False,
            enabled=False,
            connected=False,
            tool_count=None,
            visible=False,
            catalog=True,
        )
        binding["detail"] = "in Hermes's MCP catalog, not installed"
        cat.add(key, type_="mcp", binding=binding, name=name, name_rank=7, source="hermes")


def _app_key(app: Json, mcp_keys: set[str]) -> str:
    app_id = str(app.get("id"))
    server = app.get("mcp_server") if isinstance(app.get("mcp_server"), dict) else {}
    derived = (
        mcp_key(name="", command=server.get("install"), url=server.get("url")) if server else None
    )
    if derived and (upstream_for(derived) is not None or derived in mcp_keys):
        return derived
    return f"app:{app_id.lower()}"


def _add_apps(
    cat: _Catalog, vis: Visibility, apps: list[Json], mcp_keys: set[str]
) -> dict[str, str]:
    keys: dict[str, str] = {}
    for app in apps:
        app_id = str(app.get("id") or "").strip()
        if not app_id:
            continue
        key = _app_key(app, mcp_keys)
        keys[app_id.lower()] = key
        status = str(app.get("status") or "not_connected")
        connected = status == "connected"
        installed = status != "not_connected"
        native = app.get("native_tool")
        exact = [str(native)] if native else []
        auth_mode = str(app.get("auth_mode") or "")
        binding = _binding(
            "jarvis",
            "app",
            app_id,
            installed=installed,
            enabled=connected,
            connected=connected,
            tool_count=_count_tools(vis.brain_tools, exact, [f"{app_id}/"]) if connected else None,
            visible=vis.jarvis(enabled=connected, exact=exact, prefixes=[f"{app_id}/"]),
            status=status,
        )
        if app.get("unavailable_reason"):
            binding["problems"].append(str(app["unavailable_reason"])[:_ERROR_CAP])
        cat.add(
            key,
            type_="app",
            binding=binding,
            name=str(app.get("display_name") or app_id),
            name_rank=2,
            description=app.get("description"),
            source="marketplace",
            needs_credentials=auth_mode not in ("", "none", "local"),
        )
    return keys


def _count_tools(tools: set[str] | None, exact: list[str], prefixes: list[str]) -> int | None:
    if tools is None:
        return None
    pre = tuple(prefixes)
    return sum(1 for t in tools if t in exact or t.startswith(pre))


def _add_clis(cat: _Catalog, vis: Visibility, clis: list[Json]) -> dict[str, str]:
    keys: dict[str, str] = {}
    for cli in clis:
        name = str(cli.get("name") or "").strip()
        if not name:
            continue
        key = tool_name_key(name) or f"cli:{name.lower()}"
        keys[name.lower()] = key
        tool = f"cli_{name}"
        installed = cli.get("installed")
        connected = cli.get("connected")
        binding = _binding(
            "jarvis",
            "cli",
            name,
            installed=installed if isinstance(installed, bool) else None,
            enabled=None,
            connected=connected if isinstance(connected, bool) else None,
            tool_count=_count_tools(vis.brain_tools, [tool], []) if connected else None,
            visible=vis.jarvis(
                enabled=None if connected is None else bool(connected), exact=[tool]
            ),
            status=cli.get("status"),
        )
        binding["detail"] = _safe_error(cli.get("error"))
        auth = str(cli.get("auth_type") or "")
        cat.add(
            key,
            type_="cli",
            binding=binding,
            name=str(cli.get("display_name") or name),
            name_rank=3,
            description=cli.get("description"),
            source="unknown" if cli.get("is_custom") else "cli_catalog",
            upstream_url=cli.get("homepage") or None,
            needs_credentials=None if not auth else auth != "none",
        )
    return keys


def _skill_key(name: str, app_keys: dict[str, str], cli_keys: dict[str, str]) -> str:
    lowered = name.lower()
    if lowered.startswith("plugin-") and lowered[7:] in app_keys:
        return app_keys[lowered[7:]]
    if lowered.startswith("cli-") and lowered[4:] in cli_keys:
        return cli_keys[lowered[4:]]
    return tool_name_key(lowered) or cli_keys.get(lowered) or f"skill:{lowered}"


_LIVE_SKILL_STATES = frozenset(("active", "validated"))
_HERMES_SKILL_SOURCES = {
    "bundled": "hermes",
    "hub": "hermes-hub",
    "local": "local",
    "external": "local",
    "plugin": "hermes-plugin",
}


def _add_jarvis_skills(
    cat: _Catalog, skills: list[Json], app_keys: dict[str, str], cli_keys: dict[str, str]
) -> None:
    for skill in skills:
        name = str(skill.get("name") or "").strip()
        if not name:
            continue
        state = str(skill.get("state") or "").lower()
        binding = _binding(
            "jarvis",
            "skill",
            name,
            installed=True,
            enabled=state in _LIVE_SKILL_STATES,
            connected=None,
            tool_count=None,
            # Skills are not brain tools: whether the brain sees one this turn
            # depends on trigger matching, which is not knowable here.
            visible=None if state in _LIVE_SKILL_STATES else False,
            state=state or None,
        )
        cat.add(
            _skill_key(name, app_keys, cli_keys),
            type_="skill",
            binding=binding,
            name=name,
            name_rank=4,
            description=skill.get("description"),
            source="jarvis" if skill.get("is_builtin") else "local",
        )


def _add_hermes_skills(
    cat: _Catalog,
    vis: Visibility,
    inventory: Json,
    app_keys: dict[str, str],
    cli_keys: dict[str, str],
) -> None:
    for item in (inventory.get("skills") or {}).get("items") or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        enabled = item.get("enabled") is not False
        provenance = str(item.get("provenance") or "")
        binding = _binding(
            "hermes",
            "skill",
            name,
            installed=True,
            enabled=enabled,
            connected=None,
            tool_count=None,
            visible=vis.hermes(enabled=enabled, toolsets=["skills"]),
            provenance=provenance or None,
        )
        cat.add(
            _skill_key(name, app_keys, cli_keys),
            type_="skill",
            binding=binding,
            name=name,
            name_rank=6,
            source=_HERMES_SKILL_SOURCES.get(provenance, "unknown"),
        )


def _add_hermes_plugins(cat: _Catalog, vis: Visibility, inventory: Json) -> None:
    part = inventory.get("plugins") or {}
    items = part.get("items") if isinstance(part, dict) else None
    for item in items or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        enabled = str(item.get("status") or "") == "enabled"
        visible: bool | None = None
        if not enabled:
            visible = False
        elif vis.active is not None and vis.active != "hermes":
            visible = False
        # else: what an enabled plugin adds to the api_server platform is not reported
        binding = _binding(
            "hermes",
            "plugin",
            name,
            installed=True,
            enabled=enabled,
            connected=None,
            tool_count=None,
            visible=visible,
            status=item.get("status"),
            version=item.get("version") or None,
        )
        cat.add(
            f"plugin:{name.lower()}",
            type_="plugin",
            binding=binding,
            name=name,
            name_rank=6,
            description=item.get("description"),
            source="hermes-plugin",
        )


def _add_hermes_toolsets(cat: _Catalog, vis: Visibility, config: Json, inventory: Json) -> None:
    part = inventory.get("toolsets") or {}
    items: list[Json] = []
    if isinstance(part, dict) and part.get("available"):
        items = [i for i in part.get("items") or [] if isinstance(i, dict)]
    else:
        # The API server is down: fall back to the names config.yaml lists.
        platform = config.get("platform_toolsets")
        listed = _names(platform.get("api_server")) if isinstance(platform, dict) else None
        items = [{"name": n, "enabled": True, "tool_count": None} for n in listed or []]
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name or name.startswith("mcp-"):
            continue  # an MCP server's toolset belongs to that server's record
        enabled = item.get("enabled")
        enabled = enabled if isinstance(enabled, bool) else None
        count = item.get("tool_count")
        binding = _binding(
            "hermes",
            "toolset",
            name,
            installed=True,
            enabled=enabled,
            connected=None,
            tool_count=count if isinstance(count, int) else None,
            visible=vis.hermes(enabled=enabled, toolsets=[name]),
            configured=item.get("configured") if isinstance(item.get("configured"), bool) else None,
        )
        builtin = name in HERMES_TOOLSETS
        cat.add(
            f"toolset:{name.lower()}",
            type_="toolset",
            binding=binding,
            name=str(item.get("label") or name),
            name_rank=6,
            description="Hermes built-in toolset" if builtin else None,
            source="hermes" if builtin else "unknown",
            upstream_url=HERMES_UPSTREAM if builtin else None,
            needs_credentials=None,
        )


def build_catalog(
    *,
    active_brain: str | None,
    jarvis_mcp: list[Json] | None,
    jarvis_skills: list[Json] | None,
    jarvis_apps: list[Json] | None,
    jarvis_clis: list[Json] | None,
    brain_tools: Iterable[str] | None,
    hermes_config: Json | None,
    hermes_inventory: Json | None,
    verifications: dict[str, Json] | None = None,
    source_status: dict[str, Json] | None = None,
) -> Json:
    """Every canonical record, once, with its runtime bindings and honest states."""
    vis = Visibility(active_brain, brain_tools, hermes_config)
    cat = _Catalog(verifications or {})
    mcp_keys = _add_jarvis_mcp(cat, vis, jarvis_mcp or [])
    hermes_names: set[str] = set()
    if hermes_config is not None:
        hermes_keys, hermes_names = _add_hermes_mcp(cat, vis, hermes_config, hermes_inventory)
        mcp_keys |= hermes_keys
    app_keys = _add_apps(cat, vis, jarvis_apps or [], mcp_keys)
    cli_keys = _add_clis(cat, vis, jarvis_clis or [])
    _add_jarvis_skills(cat, jarvis_skills or [], app_keys, cli_keys)
    if hermes_inventory is not None and hermes_inventory.get("available"):
        _add_hermes_skills(cat, vis, hermes_inventory, app_keys, cli_keys)
        _add_hermes_plugins(cat, vis, hermes_inventory)
        _add_hermes_toolsets(cat, vis, hermes_config or {}, hermes_inventory)
        _add_hermes_mcp_catalog(cat, hermes_inventory, mcp_keys, hermes_names)

    items = [_finish(group) for group in cat.groups.values()]
    items.sort(key=lambda r: (r["states"]["installed"] is not True, r["name"].lower(), r["id"]))
    return {
        "version": CATALOG_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "active_brain": active_brain,
        "items": items,
        "total": len(items),
        "sources": source_status or {},
    }


def _haystack(record: Json) -> str:
    parts: list[str] = [
        record["id"],
        record["name"],
        *record["types"],
        record.get("description") or "",
        record.get("notes") or "",
        record.get("license") or "",
        record.get("source") or "",
    ]
    parts.extend(str(b["local_id"]) for b in record["bindings"])
    parts.extend(str(b["runtime"]) for b in record["bindings"])
    return " ".join(parts).lower()


def filter_items(
    items: list[Json], *, q: str | None = None, type_: str | None = None
) -> list[Json]:
    """Records matching every word of ``q`` and carrying ``type_`` (both optional)."""
    words = [w for w in (q or "").lower().split() if w]
    wanted = (type_ or "").strip().lower()
    out = []
    for record in items:
        if wanted and wanted not in record["types"]:
            continue
        if words:
            hay = _haystack(record)
            if not all(w in hay for w in words):
                continue
        out.append(record)
    return out


def type_counts(items: list[Json]) -> dict[str, int]:
    counts = {t: 0 for t in TYPE_ORDER}
    for record in items:
        for t in record["types"]:
            counts[t] = counts.get(t, 0) + 1
    return counts
