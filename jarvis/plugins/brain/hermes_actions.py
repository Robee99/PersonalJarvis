"""Act on Hermes Agent's inventory from the Tool Armory, through Hermes's own CLI.

The armory lists what Hermes can use (:mod:`.hermes_inventory`); this module
lets the user change it without leaving Jarvis. Every change is one ``hermes``
command, the same one a person would type, so Hermes keeps owning its config
and its install rules (its skill scanner, its MCP catalog, its plugin
permission prompt):

=================  =========  ==================================================
kind               op         command
=================  =========  ==================================================
``skill``          enable /   ``hermes config set --force skills.disabled [...]``
                   disable
``mcp``            enable /   ``hermes config set mcp_servers.<name>.enabled ..``
                   disable
``plugin``         enable     ``hermes plugins enable --no-allow-tool-override``
``plugin``         disable    ``hermes plugins disable <name>``
``toolset``        enable /   ``hermes tools enable|disable --platform api_server``
                   disable    (the platform Jarvis's runs use)
``mcp_catalog``    install    ``hermes mcp install <name>``
``skill_catalog``  install    ``hermes skills install official/<path> --yes``
=================  =========  ==================================================

A name is only accepted when the current inventory or catalog lists it for that
kind, so the route cannot be used to install an arbitrary URL or package. The
command runs without a shell, with no stdin, under a timeout; its output is
shortened and passed through ``redact_secrets`` before it is returned.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .hermes_inventory import (
    InstallFinder,
    InventoryPartError,
    _iter_skills,
    _load_config,
    clear_cache,
    collect_mcp_servers,
    collect_skills,
    find_install_dir_cli,
)

log = logging.getLogger(__name__)

#: Hermes's platform key for the API server, which every Jarvis run goes through.
API_PLATFORM: Final[str] = "api_server"
ACTION_TIMEOUT_S: Final[float] = 60.0
INSTALL_TIMEOUT_S: Final[float] = 240.0
#: Characters of command output shown to the user.
MESSAGE_CAP: Final[int] = 600

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,159}$")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

#: (kind, op) pairs the armory may ask for.
ACTIONS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("skill", "enable"),
        ("skill", "disable"),
        ("mcp", "enable"),
        ("mcp", "disable"),
        ("plugin", "enable"),
        ("plugin", "disable"),
        ("toolset", "enable"),
        ("toolset", "disable"),
        ("mcp_catalog", "install"),
        ("skill_catalog", "install"),
    }
)

Runner = Callable[[list[str], Path, float], "CommandResult"]
Lister = Callable[[Path], list[dict[str, Any]]]


class ActionRefused(ValueError):
    """The request names an unknown action or an item Hermes does not list."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    output: str


# ---------------------------------------------------------------------------
# Catalog with names and descriptions (what Hermes can add)
# ---------------------------------------------------------------------------


def _frontmatter(path: Path) -> dict[str, Any]:
    """The YAML frontmatter of a SKILL.md or the whole of a manifest (small files)."""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")[:20_000]
    except OSError:  # unreadable entry: listed without a description
        return {}
    if path.name == "SKILL.md":
        lines = text.split("\n")
        if not lines or lines[0].strip() != "---":
            return {}
        try:
            end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
        except StopIteration:  # unterminated frontmatter: no metadata
            return {}
        text = "\n".join(lines[1:end])
    try:
        import yaml

        data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 — a broken manifest is listed without a description
        return {}
    return data if isinstance(data, dict) else {}


def _one_line(value: Any, cap: int = 240) -> str:
    return " ".join(str(value or "").split())[:cap]


def collect_catalog(home: Path, *, find_install: InstallFinder | None = None) -> dict[str, Any]:
    """Every catalog MCP and optional skill, with whether it is already installed."""
    try:
        install = (find_install or find_install_dir_cli)(home)
    except InventoryPartError as exc:  # expected outage: its message is shown in the armory
        return {"available": False, "error": str(exc), "mcp_servers": [], "skills": []}
    if install is None or not install.is_dir():
        return {
            "available": False,
            "error": "Hermes's install folder was not found",
            "mcp_servers": [],
            "skills": [],
        }
    config, _error = _load_config(home)
    configured = {server["name"] for server in collect_mcp_servers(config)}
    installed_skills = {item["name"] for item in collect_skills(home, config)["items"]}

    mcps: list[dict[str, Any]] = []
    mcp_root = install / "optional-mcps"
    if mcp_root.is_dir():
        for folder in sorted(p for p in mcp_root.iterdir() if (p / "manifest.yaml").is_file()):
            manifest = _frontmatter(folder / "manifest.yaml")
            auth = manifest.get("auth")
            mcps.append(
                {
                    "name": folder.name,
                    "description": _one_line(manifest.get("description")),
                    "auth": str(auth.get("type") or "none") if isinstance(auth, dict) else "none",
                    "installed": folder.name in configured,
                }
            )

    skills: list[dict[str, Any]] = []
    skill_root = install / "optional-skills"
    for name, skill_md in _iter_skills(skill_root):
        meta = _frontmatter(skill_md)
        rel = skill_md.parent.relative_to(skill_root).as_posix()
        skills.append(
            {
                "id": f"official/{rel}",
                "name": name,
                "category": rel.split("/", 1)[0] if "/" in rel else "",
                "description": _one_line(meta.get("description")),
                "installed": name in installed_skills,
            }
        )
    skills.sort(key=lambda s: (s["category"], s["name"]))
    return {"available": True, "error": None, "mcp_servers": mcps, "skills": skills}


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def run_hermes(args: list[str], home: Path, timeout: float) -> CommandResult:
    """Run ``hermes <args>`` for ``home``; raises ``InventoryPartError`` if it cannot start."""
    from jarvis.agent_chat.runner_cli import CliUnavailable, hermes_argv_prefix
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    try:
        argv = [*hermes_argv_prefix(), *args]
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
            timeout=timeout,
            env=env,
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except subprocess.TimeoutExpired as exc:
        raise InventoryPartError(f"hermes {args[0]} took too long and was stopped") from exc
    except OSError as exc:
        raise InventoryPartError(f"hermes {args[0]} could not start") from exc
    return CommandResult(proc.returncode, (proc.stdout or "") + (proc.stderr or ""))


def _summary(output: str) -> str:
    from jarvis.core.redact import redact_secrets

    lines = [_ANSI_RE.sub("", line).strip() for line in output.splitlines()]
    lines = [line for line in lines if line and not line.startswith("(Use --force")]
    text = "\n".join(lines[-6:])
    return redact_secrets(text)[-MESSAGE_CAP:]


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_RE.match(name) or ".." in name:
        raise ActionRefused("That name is not valid.")
    return name


def _known(
    kind: str,
    name: str,
    home: Path,
    *,
    list_plugins: Lister | None,
    fetch_toolsets: Lister | None,
    find_install: InstallFinder | None,
) -> None:
    """Refuse a name the current inventory or catalog does not list for ``kind``."""
    from .hermes_inventory import fetch_toolsets_http, list_plugins_cli

    config, _error = _load_config(home)
    if kind == "skill":
        names = {item["name"] for item in collect_skills(home, config)["items"]}
    elif kind == "mcp":
        names = {server["name"] for server in collect_mcp_servers(config)}
    elif kind == "plugin":
        names = {str(row.get("name")) for row in (list_plugins or list_plugins_cli)(home)}
    elif kind == "toolset":
        names = {str(row.get("name")) for row in (fetch_toolsets or fetch_toolsets_http)(home)}
    else:
        catalog = collect_catalog(home, find_install=find_install)
        if not catalog["available"]:
            raise InventoryPartError(catalog["error"] or "Hermes's catalog could not be read")
        key, field = ("mcp_servers", "name") if kind == "mcp_catalog" else ("skills", "id")
        names = {entry[field] for entry in catalog[key]}
    if name not in names:
        raise ActionRefused(f"Hermes does not list {name!r} here.")


def _disabled_skills(config: dict[str, Any]) -> list[str]:
    skills_cfg = config.get("skills")
    raw = skills_cfg.get("disabled") if isinstance(skills_cfg, dict) else None
    if isinstance(raw, str):
        raw = [raw]
    return sorted({str(s).strip() for s in raw or [] if str(s).strip()})


def _command(kind: str, op: str, name: str, home: Path) -> tuple[list[str], float]:
    if kind == "skill":
        disabled = set(_disabled_skills(_load_config(home)[0]))
        disabled = disabled - {name} if op == "enable" else disabled | {name}
        value = json.dumps(sorted(disabled))
        return ["config", "set", "--force", "skills.disabled", value], ACTION_TIMEOUT_S
    if kind == "mcp":
        flag = "true" if op == "enable" else "false"
        return ["config", "set", f"mcp_servers.{name}.enabled", flag], ACTION_TIMEOUT_S
    if kind == "plugin":
        if op == "enable":
            return ["plugins", "enable", "--no-allow-tool-override", name], ACTION_TIMEOUT_S
        return ["plugins", "disable", name], ACTION_TIMEOUT_S
    if kind == "toolset":
        return ["tools", op, "--platform", API_PLATFORM, name], ACTION_TIMEOUT_S
    if kind == "mcp_catalog":
        return ["mcp", "install", name], INSTALL_TIMEOUT_S
    return ["skills", "install", name, "--yes"], INSTALL_TIMEOUT_S


#: Changes a running Hermes gateway only loads on restart. Toolsets and skills
#: are read for every new run, and the gateway reconciles MCP servers with
#: ``config.yaml`` on its own housekeeping tick; plugins load at start-up.
_NEEDS_RESTART: Final[frozenset[str]] = frozenset({"plugin"})


def run_action(
    kind: str,
    op: str,
    name: str,
    home: Path,
    *,
    run: Runner | None = None,
    list_plugins: Lister | None = None,
    fetch_toolsets: Lister | None = None,
    find_install: InstallFinder | None = None,
) -> dict[str, Any]:
    """Do one armory action; raises :class:`ActionRefused` or ``InventoryPartError``."""
    if (kind, op) not in ACTIONS:
        raise ActionRefused(f"Unknown action: {kind} {op}.")
    name = _check_name(name)
    if kind == "mcp" and "." in name:  # the name becomes a dotted config key
        raise ActionRefused("That name is not valid.")
    _known(
        kind,
        name,
        home,
        list_plugins=list_plugins,
        fetch_toolsets=fetch_toolsets,
        find_install=find_install,
    )
    args, timeout = _command(kind, op, name, home)
    result = (run or run_hermes)(args, home, timeout)
    clear_cache()
    ok = result.returncode == 0
    log.info("hermes armory action %s %s %s: exit %s", kind, op, name, result.returncode)
    return {
        "ok": ok,
        "kind": kind,
        "op": op,
        "name": name,
        "message": _summary(result.output) or ("Done." if ok else "Hermes reported an error."),
        "restart_needed": ok and kind in _NEEDS_RESTART,
    }
