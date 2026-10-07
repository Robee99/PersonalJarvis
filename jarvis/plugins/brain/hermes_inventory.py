"""Hermes Agent's capability inventory, discovered at runtime for the Tool Armory.

Jarvis's own counts (apps, MCP servers, skills) say nothing about what Hermes
can do when it is the brain. This module reads Hermes's real inventory from the
places Hermes itself keeps it, never from a hard-coded number:

* **Skills** from disk: ``HERMES_HOME/skills`` (local store) plus every
  ``skills.external_dirs`` entry of ``config.yaml``. A skill is a directory with
  a ``SKILL.md``. Provenance mirrors Hermes's ``tools/skill_usage.provenance``:
  ``hub`` when ``skills/.hub/lock.json`` lists it (by key or ``install_path``),
  ``bundled`` when ``skills/.bundled_manifest`` (or ``.curator_suppressed``)
  names it, ``external`` when it only lives in an external dir, else ``local``
  (agent-created or hand-made). ``plugin`` skills are the ``SKILL.md`` files
  shipped inside user-installed plugin folders (``HERMES_HOME/plugins``),
  named ``<plugin>:<skill>`` as Hermes namespaces them.
* **MCP servers** from ``mcp_servers`` in ``config.yaml``: configured and
  enabled only. The API server has no live MCP status endpoint, so connection
  state is reported as not checked rather than guessed.
* **Plugins** from ``hermes plugins list --json`` (subprocess, with timeout).
* **Toolsets** from the Hermes API server's ``GET /v1/toolsets`` (loopback
  only, Bearer ``API_SERVER_KEY`` read in place from Hermes's ``.env``).

Every part fails on its own: an unreachable API server leaves the skills and
MCP counts intact and says why the toolsets are missing. Nothing secret leaves
this module: no env values, no MCP headers/args/URLs, no API keys.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

#: How long one inventory answer is reused (the armory polls on mount).
CACHE_TTL_S = 30.0
#: Most skill rows returned; the counts always cover every skill.
ITEM_CAP = 500
#: Upper bound on SKILL.md files scanned per root (a runaway external dir).
SCAN_CAP = 5000
PLUGINS_TIMEOUT_S = 20.0
TOOLSETS_TIMEOUT_S = 4.0

NOT_FOUND_REASON = "Hermes not found on this computer"

# Mirrors agent/skill_utils.py in Hermes: never a skill, wherever they appear.
_EXCLUDED_DIRS = frozenset((
    ".git", ".github", ".hub", ".archive", ".curator_backups", ".locks",
    ".venv", "venv", "node_modules", "site-packages", "__pycache__",
    ".tox", ".nox", ".pytest_cache", ".mypy_cache", ".ruff_cache",
))
# Support folders inside a skill package (their own SKILL.md files are not skills).
_SUPPORT_DIRS = frozenset(("references", "templates", "assets", "scripts"))
_LOOPBACK_HOSTS = frozenset(("127.0.0.1", "localhost", "::1"))

ToolsetFetcher = Callable[[Path], list[dict[str, Any]]]
PluginLister = Callable[[Path], list[dict[str, Any]]]
InstallFinder = Callable[[Path], Path | None]


class InventoryPartError(Exception):
    """One part of the inventory is unavailable; the message is shown to the user."""


# ---------------------------------------------------------------------------
# Hermes home
# ---------------------------------------------------------------------------


def find_hermes_home(candidates: list[Path] | None = None) -> Path | None:
    """The first Hermes home that exists, in Hermes's own lookup order."""
    if candidates is None:
        from .hermes import _hermes_home_candidates

        candidates = _hermes_home_candidates()
    for home in candidates:
        try:
            if home.is_dir():
                return home
        except OSError:  # unreadable candidate (permissions, bad drive): try the next one
            continue
    return None


def _load_config(home: Path) -> tuple[dict[str, Any], str | None]:
    """``config.yaml`` as a dict and an error text when it could not be read."""
    path = home / "config.yaml"
    if not path.is_file():
        return {}, None
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception as exc:  # noqa: BLE001 — any YAML/OS failure becomes a visible config_error
        log.warning("hermes inventory: config.yaml unreadable: %s", type(exc).__name__)
        return {}, "Hermes config.yaml could not be read"
    return (data if isinstance(data, dict) else {}), None


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


def _read_lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:  # missing/unreadable metadata file means "no entries", as in Hermes
        return []
    return [s for s in (line.strip() for line in text.splitlines()) if s]


def _skill_name(skill_md: Path, fallback: str) -> str:
    """The frontmatter ``name:`` of a SKILL.md (first 4000 chars), else ``fallback``."""
    try:
        head = skill_md.read_text(encoding="utf-8-sig", errors="replace")[:4000]
    except OSError:  # unreadable SKILL.md still counts, under its folder name
        return fallback
    lines = [line.strip() for line in head.split("\n")]
    if "---" not in lines:
        return fallback
    block = lines[lines.index("---") + 1 :]
    block = block[: block.index("---")] if "---" in block else block
    for line in block:
        if line.startswith("name:"):
            value = line.split(":", 1)[1].strip().strip("\"'")
            if value:
                return value
    return fallback


def _is_excluded(skill_md: Path, root: Path) -> bool:
    try:
        parts = skill_md.relative_to(root).parts
    except ValueError:  # outside the root (symlink escape): not part of this tree
        return True
    if any(part in _EXCLUDED_DIRS for part in parts):
        return True
    return any(
        part in _SUPPORT_DIRS and (root.joinpath(*parts[:idx]) / "SKILL.md").exists()
        for idx, part in enumerate(parts[:-1])
    )


def _iter_skills(root: Path) -> Iterator[tuple[str, Path]]:
    """``(name, SKILL.md)`` for every skill under ``root``."""
    if not root.is_dir():
        return
    seen = 0
    try:
        for skill_md in root.rglob("SKILL.md"):
            if _is_excluded(skill_md, root):
                continue
            seen += 1
            if seen > SCAN_CAP:
                log.warning("hermes inventory: stopped scanning %s after %d skills", root, SCAN_CAP)
                return
            yield _skill_name(skill_md, fallback=skill_md.parent.name), skill_md
    except OSError as exc:
        log.warning("hermes inventory: skill scan of %s stopped: %s", root, exc)


def _bundled_names(skills_dir: Path) -> set[str]:
    lines = _read_lines(skills_dir / ".bundled_manifest")
    names = {n for n in (line.split(":", 1)[0].strip() for line in lines) if n}
    suppressed = _read_lines(skills_dir / ".curator_suppressed")
    return names | {line for line in suppressed if not line.startswith("#")}


def _hub_names(skills_dir: Path) -> set[str]:
    lock_path = skills_dir / ".hub" / "lock.json"
    if not lock_path.is_file():
        return set()
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("hermes inventory: hub lock unreadable: %s", exc)
        return set()
    installed = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(installed, dict):
        return set()
    names = {str(k) for k in installed}
    base = skills_dir.resolve()
    for entry in installed.values():
        install_path = entry.get("install_path") if isinstance(entry, dict) else None
        if not isinstance(install_path, str) or not install_path.strip():
            continue
        try:
            resolved = (skills_dir / install_path).resolve()
            resolved.relative_to(base)
        except (OSError, ValueError):  # install_path escapes the skills dir: ignored, as in Hermes
            continue
        if (resolved / "SKILL.md").is_file():
            names.add(_skill_name(resolved / "SKILL.md", fallback=resolved.name))
    return names


def _external_dirs(home: Path, config: dict[str, Any]) -> list[Path]:
    skills_cfg = config.get("skills")
    raw = skills_cfg.get("external_dirs") if isinstance(skills_cfg, dict) else None
    entries = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    local = (home / "skills").resolve()
    out: list[Path] = []
    for entry in entries:
        text = str(entry).strip()
        if not text:
            continue
        path = Path(os.path.expanduser(os.path.expandvars(text)))
        if not path.is_absolute():
            path = home / path
        try:
            path = path.resolve()
        except OSError:  # unresolvable entry: Hermes skips it too
            continue
        if path != local and path not in out and path.is_dir():
            out.append(path)
    return out


def _plugin_namespace(plugin_dir: Path) -> str:
    manifest = plugin_dir / "plugin.yaml"
    if manifest.is_file():
        try:
            import yaml

            data = yaml.safe_load(manifest.read_text(encoding="utf-8-sig", errors="replace"))
        except Exception:  # noqa: BLE001 — a broken manifest keeps the folder name as namespace
            data = None
        if isinstance(data, dict):
            name = data.get("skill_namespace") or data.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return plugin_dir.name


def _plugin_skills(home: Path) -> list[str]:
    plugins_dir = home / "plugins"
    if not plugins_dir.is_dir():
        return []
    names: list[str] = []
    try:
        plugin_dirs = sorted(p for p in plugins_dir.iterdir() if p.is_dir())
    except OSError as exc:
        log.warning("hermes inventory: plugins folder unreadable: %s", exc)
        return []
    for plugin_dir in plugin_dirs:
        if plugin_dir.name in _EXCLUDED_DIRS or plugin_dir.name.startswith("."):
            continue
        namespace = _plugin_namespace(plugin_dir)
        names.extend(f"{namespace}:{name}" for name, _md in _iter_skills(plugin_dir))
    return names


def collect_skills(home: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Skill counts by provenance plus up to ``ITEM_CAP`` named rows."""
    skills_dir = home / "skills"
    hub, bundled = _hub_names(skills_dir), _bundled_names(skills_dir)

    def origin(name: str, fallback: str) -> str:
        return "hub" if name in hub else "bundled" if name in bundled else fallback

    rows: dict[str, str] = {}
    external_resolved = _external_dirs(home, config)
    for name, skill_md in _iter_skills(skills_dir):
        # An external dir mounted inside the local tree is not local (Hermes: local_only scan).
        if any(_within(skill_md, ext) for ext in external_resolved):
            continue
        rows.setdefault(name, origin(name, "local"))
    for ext in external_resolved:
        for name, _md in _iter_skills(ext):
            rows.setdefault(name, origin(name, "external"))  # the local copy wins on a clash
    for name in _plugin_skills(home):
        rows.setdefault(name, "plugin")

    counts = {key: 0 for key in ("bundled", "hub", "local", "external", "plugin")}
    for provenance in rows.values():
        counts[provenance] += 1
    items = [{"name": n, "provenance": p} for n, p in sorted(rows.items())]
    return {
        **counts,
        "total": len(rows),
        "items": items[:ITEM_CAP],
        "truncated": len(items) > ITEM_CAP,
    }


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except (OSError, ValueError):  # not under root (or unresolvable): treat as outside
        return False
    return True


# ---------------------------------------------------------------------------
# MCP servers (config only)
# ---------------------------------------------------------------------------

_TRUE_WORDS = frozenset(("true", "1", "yes", "on"))
_FALSE_WORDS = frozenset(("false", "0", "no", "off"))


def _boolish(value: Any, default: bool = True) -> bool:
    """Hermes's ``mcp_server_enabled`` parsing: absent/unparseable means on."""
    if value is None:
        return default
    if isinstance(value, (bool, int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_WORDS:
            return True
        if lowered in _FALSE_WORDS:
            return False
    return default


def _name_list(raw: Any) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return None
    return [str(item).strip() for item in raw if str(item).strip()]


def collect_mcp_servers(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Configured MCP servers: name, transport, enabled and tool filter. No secrets."""
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        return []
    out: list[dict[str, Any]] = []
    for name, entry in sorted(servers.items(), key=lambda kv: str(kv[0])):
        cfg = entry if isinstance(entry, dict) else {}
        has_url, has_command = bool(cfg.get("url")), bool(cfg.get("command"))
        transport = (
            "invalid" if has_url and has_command
            else "http" if has_url
            else "stdio" if has_command
            else "unknown"
        )
        tools = cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}
        out.append({
            "name": str(name),
            "transport": transport,
            "enabled": _boolish(cfg.get("enabled", True)),
            "tools_filter": {
                "include": _name_list(tools.get("include")),
                "exclude": _name_list(tools.get("exclude")),
            },
        })
    return out


# ---------------------------------------------------------------------------
# Plugins (hermes plugins list --json)
# ---------------------------------------------------------------------------


def list_plugins_cli(home: Path) -> list[dict[str, Any]]:
    """Rows of ``hermes plugins list --json``; raises ``InventoryPartError``."""
    from jarvis.agent_chat.runner_cli import CliUnavailable, hermes_argv_prefix
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    try:
        argv = [*hermes_argv_prefix(), "plugins", "list", "--json"]
    except CliUnavailable as exc:
        raise InventoryPartError("Hermes command line (hermes) is not on PATH") from exc
    env = {**os.environ, "HERMES_HOME": str(home), "NO_COLOR": "1"}
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=PLUGINS_TIMEOUT_S,
            env=env,
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except subprocess.TimeoutExpired as exc:
        raise InventoryPartError("hermes plugins list timed out") from exc
    except OSError as exc:
        raise InventoryPartError("hermes plugins list could not start") from exc
    out = proc.stdout or ""
    if "No plugins installed" in out:
        return []  # Hermes prints prose instead of JSON when there is nothing to list
    if proc.returncode != 0:
        raise InventoryPartError(f"hermes plugins list failed (exit {proc.returncode})")
    start = out.find("[")
    try:
        rows = json.loads(out[start:]) if start >= 0 else None
    except json.JSONDecodeError as exc:
        raise InventoryPartError("hermes plugins list returned unreadable output") from exc
    if not isinstance(rows, list):
        raise InventoryPartError("hermes plugins list returned unreadable output")
    return [row for row in rows if isinstance(row, dict)]


def _plugins_part(home: Path, lister: PluginLister) -> dict[str, Any]:
    try:
        rows = lister(home)
    except InventoryPartError as exc:  # expected outage: its message is shown in the armory
        return {"available": False, "enabled": 0, "total": 0, "items": [], "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — one broken part must not hide the others
        log.warning("hermes inventory: plugin listing failed: %s", exc)
        return {"available": False, "enabled": 0, "total": 0, "items": [],
                "error": "Hermes plugins could not be listed"}
    items = [
        {
            "name": str(row.get("name") or ""),
            "status": str(row.get("status") or "unknown"),
            "version": str(row.get("version") or ""),
            "source": str(row.get("source") or ""),
            "description": str(row.get("description") or "")[:200],
        }
        for row in rows
        if row.get("name")
    ]
    return {
        "available": True,
        "enabled": sum(1 for item in items if item["status"] == "enabled"),
        "total": len(items),
        "items": items,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Catalog: what Hermes ships and can add (optional MCPs, optional skills)
# ---------------------------------------------------------------------------


def find_install_dir_cli(home: Path) -> Path | None:
    """Hermes's install directory, from ``hermes --version``; raises ``InventoryPartError``."""
    from jarvis.agent_chat.runner_cli import CliUnavailable, hermes_argv_prefix
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    try:
        argv = [*hermes_argv_prefix(), "--version"]
    except CliUnavailable as exc:
        raise InventoryPartError("Hermes command line (hermes) is not on PATH") from exc
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=PLUGINS_TIMEOUT_S,
            env={**os.environ, "HERMES_HOME": str(home), "NO_COLOR": "1"},
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise InventoryPartError("hermes --version could not run") from exc
    for line in (proc.stdout or "").splitlines():
        label, _, value = line.partition(":")
        if label.strip().lower() == "install directory" and value.strip():
            return Path(value.strip())
    return None


def _count_skill_files(root: Path) -> int:
    return sum(1 for _ in _iter_skills(root)) if root.is_dir() else 0


def _catalog_part(home: Path, finder: InstallFinder) -> dict[str, Any]:
    empty = {"available": False, "mcp_servers": 0, "optional_skills": 0, "bundled_skills": 0}
    try:
        install = finder(home)
    except InventoryPartError as exc:  # expected outage: its message is shown in the armory
        return {**empty, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — one broken part must not hide the others
        log.warning("hermes inventory: catalog lookup failed: %s", exc)
        return {**empty, "error": "Hermes's catalog could not be read"}
    if install is None or not install.is_dir():
        return {**empty, "error": "Hermes's install folder was not found"}
    mcps = install / "optional-mcps"
    mcp_names = sorted(
        d.name for d in mcps.iterdir() if (d / "manifest.yaml").is_file()
    ) if mcps.is_dir() else []
    return {
        "available": True,
        "mcp_servers": len(mcp_names),
        "mcp_names": mcp_names[:ITEM_CAP],
        "optional_skills": _count_skill_files(install / "optional-skills"),
        "bundled_skills": _count_skill_files(install / "skills"),
        "error": None,
    }


# ---------------------------------------------------------------------------
# Toolsets (Hermes API server, loopback only)
# ---------------------------------------------------------------------------


def fetch_toolsets_http(home: Path) -> list[dict[str, Any]]:
    """``GET /v1/toolsets`` rows; raises ``InventoryPartError``."""
    import httpx

    from .hermes import configured_base_url, read_api_server_key

    base = configured_base_url().rstrip("/")
    host = (urlsplit(base).hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        raise InventoryPartError("Hermes API server is not on this computer; not queried")
    key = read_api_server_key([home])
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        response = httpx.get(f"{base}/v1/toolsets", headers=headers, timeout=TOOLSETS_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise InventoryPartError(
            "Hermes API server is not reachable (is `hermes gateway` running?)"
        ) from exc
    if response.status_code in (401, 403):
        raise InventoryPartError("Hermes API server refused the key (check API_SERVER_KEY)")
    if response.status_code != 200:
        raise InventoryPartError(f"Hermes API server answered HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise InventoryPartError("Hermes API server returned unreadable toolsets") from exc
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise InventoryPartError("Hermes API server returned unreadable toolsets")
    return [row for row in data if isinstance(row, dict)]


def _toolsets_part(home: Path, fetcher: ToolsetFetcher) -> dict[str, Any]:
    try:
        rows = fetcher(home)
    except InventoryPartError as exc:  # expected outage: its message is shown in the armory
        return {"available": False, "enabled": 0, "total": 0, "items": [], "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — one broken part must not hide the others
        log.warning("hermes inventory: toolset fetch failed: %s", exc)
        return {"available": False, "enabled": 0, "total": 0, "items": [],
                "error": "Hermes toolsets could not be read"}
    items = [
        {
            "name": str(row.get("name") or ""),
            "label": str(row.get("label") or row.get("name") or ""),
            "enabled": bool(row.get("enabled")),
            "configured": bool(row.get("configured")),
            "tool_count": len(row.get("tools") or []) if isinstance(row.get("tools"), list) else 0,
        }
        for row in rows
        if row.get("name")
    ]
    return {
        "available": True,
        "enabled": sum(1 for item in items if item["enabled"]),
        "total": len(items),
        "items": items,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()


def _empty_inventory(reason: str) -> dict[str, Any]:
    missing = {"available": False, "enabled": 0, "total": 0, "items": [], "error": reason}
    return {
        "available": False,
        "reason": reason,
        "home": None,
        "config_error": None,
        "skills": {"bundled": 0, "hub": 0, "local": 0, "external": 0, "plugin": 0,
                   "total": 0, "items": [], "truncated": False},
        "mcp_servers": [],
        "mcp_status_checked": False,
        "plugins": dict(missing),
        "toolsets": dict(missing),
        "catalog": {"available": False, "mcp_servers": 0, "optional_skills": 0,
                    "bundled_skills": 0, "error": reason},
    }


def collect_hermes_inventory(
    home: Path | None = None,
    *,
    fetch_toolsets: ToolsetFetcher | None = None,
    list_plugins: PluginLister | None = None,
    find_install: InstallFinder | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Hermes's capability inventory; every part degrades on its own.

    ``fetch_toolsets``/``list_plugins`` are injectable for tests; the cache is
    only used with the real (default) fetchers.
    """
    resolved = home if home is not None else find_hermes_home()
    if resolved is None or not resolved.is_dir():
        return _empty_inventory(NOT_FOUND_REASON)
    cacheable = (
        use_cache and fetch_toolsets is None and list_plugins is None and find_install is None
    )
    cache_key = str(resolved)
    if cacheable:
        with _CACHE_LOCK:
            hit = _CACHE.get(cache_key)
        if hit is not None and time.monotonic() - hit[0] < CACHE_TTL_S:
            return hit[1]

    config, config_error = _load_config(resolved)
    inventory = {
        "available": True,
        "reason": None,
        "home": str(resolved),
        "config_error": config_error,
        "skills": collect_skills(resolved, config),
        "mcp_servers": collect_mcp_servers(config),
        # The API server exposes no live MCP status: configured/enabled only.
        "mcp_status_checked": False,
        "plugins": _plugins_part(resolved, list_plugins or list_plugins_cli),
        "toolsets": _toolsets_part(resolved, fetch_toolsets or fetch_toolsets_http),
        "catalog": _catalog_part(resolved, find_install or find_install_dir_cli),
    }
    if cacheable:
        with _CACHE_LOCK:
            _CACHE[cache_key] = (time.monotonic(), inventory)
    return inventory


def clear_cache() -> None:
    """Drop cached inventories (tests, or after the user changes Hermes)."""
    with _CACHE_LOCK:
        _CACHE.clear()
