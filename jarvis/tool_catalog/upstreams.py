"""Canonical upstream keys and the static metadata table for known upstreams.

A record's identity is the upstream it runs, derived from what the runtime
would launch or call: the package of a ``npx``/``uvx`` command, the executable
of a direct command, the module or script of a Python launch, or the host of a
remote URL. Local names are only a fallback (``windows`` in Hermes and
``windows-mcp`` in Jarvis both launch ``windows-mcp``).

Only the derived key leaves this module: never an argument, a path, a query
string or a header.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

#: Launchers whose first real argument is the package or script they run.
_LAUNCHERS = frozenset(
    (
        "npx",
        "uvx",
        "bunx",
        "pnpx",
        "pipx",
        "uv",
        "pnpm",
        "yarn",
        "npm",
        "node",
        "python",
        "python3",
        "py",
        "pythonw",
        "deno",
        "bun",
        "cmd",
    )
)
#: Sub-commands a launcher takes before the package (``uv tool run x``).
_SUBCOMMANDS = frozenset(("run", "tool", "dlx", "exec", "x", "start"))
#: Flags whose next argument names the package outright.
_PACKAGE_FLAGS = frozenset(("--from", "--package", "-p", "--with"))
_EXTENSIONS = (".exe", ".cmd", ".bat", ".ps1", ".py", ".js", ".mjs", ".cjs")
_LOOPBACK = frozenset(("127.0.0.1", "localhost", "::1"))
_VERSION_PIN = re.compile(r"(==|>=|<=|~=).*$")


@dataclass(frozen=True, slots=True)
class Upstream:
    """What is known about one upstream, independent of any runtime."""

    name: str
    upstream_url: str | None = None
    license: str | None = None
    needs_credentials: bool | None = None
    notes: str | None = None
    #: ``canonical`` (the route to prefer), ``primary``/``secondary`` for two
    #: routes to the same capability, else ``None``.
    role: str | None = None
    #: The canonical key of the record this one duplicates in capability.
    duplicate_of: str | None = None
    #: ``known`` for verified metadata, ``local`` for a local script/bridge.
    source: str = "known"


#: Hermes Agent's own repository, for its built-in toolsets.
HERMES_UPSTREAM = "https://github.com/NousResearch/hermes-agent"

KNOWN: dict[str, Upstream] = {
    "windows-mcp": Upstream(
        "Windows-MCP",
        "https://github.com/CursorTouch/Windows-MCP",
        "MIT",
        False,
        "Canonical desktop control route.",
        role="canonical",
    ),
    "@playwright/mcp": Upstream(
        "Playwright MCP",
        "https://github.com/microsoft/playwright-mcp",
        "Apache-2.0",
        False,
        "Secondary browser route; the Playwright CLI and its skill are the primary one.",
        role="secondary",
        duplicate_of="playwright-cli",
    ),
    "playwright-cli": Upstream(
        "Playwright CLI",
        "https://github.com/microsoft/playwright-cli",
        "Apache-2.0",
        False,
        "Primary browser route (command line plus skill).",
        role="primary",
    ),
    "mcp-server-fetch": Upstream(
        "Fetch",
        "https://github.com/modelcontextprotocol/servers",
        "MIT",
        False,
        "Reference MCP server that fetches a URL as Markdown.",
    ),
    "context7": Upstream(
        "Context7",
        "https://github.com/upstash/context7",
        "MIT",
        False,
        "Hosted library documentation lookup.",
    ),
    "deepwiki": Upstream(
        "DeepWiki",
        "https://mcp.deepwiki.com",
        None,
        False,
        "Free hosted service by Cognition; license not published.",
    ),
    "microsoft-learn": Upstream(
        "Microsoft Learn",
        "https://github.com/MicrosoftDocs/mcp",
        "MIT",
        False,
        "Hosted Microsoft documentation search.",
    ),
    "markitdown-mcp": Upstream(
        "MarkItDown",
        "https://github.com/microsoft/markitdown",
        "MIT",
        False,
        "Converts documents to Markdown.",
    ),
    "strata": Upstream(
        "Strata",
        None,
        None,
        None,
        "Local script; upstream unverified.",
        source="local",
    ),
    "jarvis-bridge": Upstream(
        "Jarvis bridge",
        None,
        None,
        True,
        "Jarvis's own tools offered to Hermes over MCP on this computer "
        "(needs the Jarvis Control API key).",
        source="local",
    ),
    "firecrawl-mcp": Upstream(
        "Firecrawl",
        "https://github.com/firecrawl/firecrawl-mcp-server",
        "MIT",
        True,
        "Optional; the hosted path needs a Firecrawl API key.",
    ),
    "@apify/actors-mcp-server": Upstream(
        "Apify Actors",
        "https://github.com/apify/actors-mcp-server",
        None,
        True,
        "Needs an Apify account and credits; keep disabled unless used.",
    ),
    "@paperclipai/mcp-server": Upstream(
        "Paperclip",
        None,
        None,
        True,
        "Needs the local Paperclip service and a board key.",
    ),
}

#: Hermes built-in toolsets (``platform_toolsets``), shown as type ``toolset``.
HERMES_TOOLSETS = frozenset(
    (
        "browser",
        "code_execution",
        "computer_use",
        "connections",
        "cronjob",
        "delegation",
        "file",
        "memory",
        "session_search",
        "skills",
        "terminal",
        "todo",
        "video",
        "vision",
        "web",
    )
)

#: Package/executable names that are another spelling of a canonical key.
_PACKAGE_ALIASES = {
    "windows-mcp": "windows-mcp",
    "@playwright/mcp": "@playwright/mcp",
    "mcp-server-fetch": "mcp-server-fetch",
    "markitdown-mcp": "markitdown-mcp",
    "strata_mcp": "strata",
    "strata-mcp": "strata",
    "firecrawl-mcp": "firecrawl-mcp",
    "@apify/actors-mcp-server": "@apify/actors-mcp-server",
    "@paperclipai/mcp-server": "@paperclipai/mcp-server",
    "@upstash/context7-mcp": "context7",
    "playwright-cli": "playwright-cli",
    "@playwright/cli": "playwright-cli",
}

#: Hosts of hosted MCP servers.
_HOST_KEYS = {
    "learn.microsoft.com": "microsoft-learn",
    "mcp.deepwiki.com": "deepwiki",
    "mcp.context7.com": "context7",
    "mcp.firecrawl.dev": "firecrawl-mcp",
    "mcp.apify.com": "@apify/actors-mcp-server",
}

#: Local names, used only when nothing launchable can be read (a catalog entry
#: that is not installed yet, or a config entry with no command or URL).
_NAME_KEYS = {
    "windows": "windows-mcp",
    "windows-mcp": "windows-mcp",
    "playwright": "@playwright/mcp",
    "playwright-mcp": "@playwright/mcp",
    "fetch": "mcp-server-fetch",
    "context7": "context7",
    "deepwiki": "deepwiki",
    "microsoft-learn": "microsoft-learn",
    "markitdown": "markitdown-mcp",
    "strata": "strata",
    "jarvis": "jarvis-bridge",
    "firecrawl": "firecrawl-mcp",
    "firecrawl-mcp": "firecrawl-mcp",
    "apify": "@apify/actors-mcp-server",
    "paperclip": "@paperclipai/mcp-server",
}

#: CLI and skill names that belong to a canonical upstream.
_TOOL_NAME_KEYS = {
    "playwright-cli": "playwright-cli",
    "playwright": "playwright-cli",
}


def _looks_like_path(token: str) -> bool:
    return (
        "\\" in token
        or token.startswith(("/", "./", "../", "~"))
        or bool(re.match(r"^[A-Za-z]:", token))
        or token.lower().endswith(_EXTENSIONS)
    )


def normalize_package(token: str) -> str:
    """``@playwright/mcp@latest`` → ``@playwright/mcp``; ``C:\\x\\tool.exe`` → ``tool``."""
    text = token.strip().strip("\"'")
    if _looks_like_path(text):
        text = re.split(r"[\\/]", text.rstrip("\\/"))[-1]
        lowered = text.lower()
        for ext in _EXTENSIONS:
            if lowered.endswith(ext):
                text = text[: -len(ext)]
                break
    if text.startswith("@"):
        scope, _, rest = text[1:].partition("/")
        text = "@" + scope + ("/" + rest.split("@", 1)[0] if rest else "")
    else:
        text = text.split("@", 1)[0]
    return _VERSION_PIN.sub("", text).strip().lower()


def package_from_argv(argv: list[str]) -> str | None:
    """The package, executable, module or script an argv launches."""
    tokens = [str(t) for t in argv if str(t).strip()]
    if not tokens:
        return None
    head = normalize_package(tokens[0])
    if head not in _LAUNCHERS:
        return head or None
    rest = tokens[1:]
    i = 0
    while i < len(rest):
        token = rest[i]
        lowered = token.lower()
        if lowered in _PACKAGE_FLAGS or lowered == "-m":
            return normalize_package(rest[i + 1]) if i + 1 < len(rest) else None
        if token.startswith("-") or (token.startswith("/") and len(token) <= 3):  # -y, /c
            i += 1
            continue
        if lowered in _SUBCOMMANDS:
            i += 1
            continue
        name = normalize_package(token)
        if name in _LAUNCHERS:  # ``cmd /c npx -y pkg``: the real launcher follows
            return package_from_argv(rest[i:])
        return name or None
    return None


def key_from_url(url: str) -> str | None:
    """The canonical key of a remote MCP URL: a known host, the Jarvis bridge, or the host."""
    try:
        parts = urlsplit(str(url).strip())
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:  # malformed URL (bad port): no key rather than a crash
        return None
    if not host:
        return None
    if host in _HOST_KEYS:
        return _HOST_KEYS[host]
    for known, key in _HOST_KEYS.items():
        if host.endswith("." + known):
            return key
    if host in _LOOPBACK:
        if parts.path.startswith("/api/control/mcp"):
            return "jarvis-bridge"
        return f"url:{host}:{port}" if port else f"url:{host}"
    return f"url:{host}"


def mcp_key(*, name: str, command: Any = None, args: Any = None, url: Any = None) -> str | None:
    """Canonical key for an MCP server entry, or ``None`` when nothing identifies it."""
    if isinstance(url, str) and url.strip():
        key = key_from_url(url)
        if key:
            return key
    argv: list[str] = []
    if isinstance(command, str) and command.strip():
        argv.append(command)
    elif isinstance(command, list):
        argv.extend(str(c) for c in command)
    if isinstance(args, list):
        argv.extend(str(a) for a in args)
    package = package_from_argv(argv) if argv else None
    if package:
        return _PACKAGE_ALIASES.get(package, package)
    return name_key(name)


def name_key(name: str) -> str | None:
    """The canonical key a bare local MCP name stands for, if it is a known one."""
    return _NAME_KEYS.get(str(name).strip().lower())


def tool_name_key(name: str) -> str | None:
    """The canonical key of a CLI or skill name that belongs to a known upstream."""
    return _TOOL_NAME_KEYS.get(str(name).strip().lower())


def upstream_for(key: str) -> Upstream | None:
    return KNOWN.get(key)
