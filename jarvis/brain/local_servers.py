"""Local model servers on this computer, found and connected in one click.

A local model (Qwen or Gemma under llama.cpp's ``llama-server``, LM Studio,
vLLM, Ollama) is an OpenAI-compatible HTTP server on a loopback port. Before
this module the owner had to know three things to use one: that the "Local
server (OpenAI-compatible)" card is the llama.cpp slot, which port the server
listens on, and that Hermes (the brain, ADR-0042) needs the same server as its
own provider in ``config.yaml``. This module answers all three:

* :func:`scan` probes the usual ports, the servers Hermes already knows and the
  URL on Jarvis's local card, and reports each one as running, loading (a
  ``llama-server`` still reading its model, HTTP 503) or not running, with the
  models it serves and the Hermes provider that points at it.
* :func:`connect` makes a server Hermes's provider (``providers.<name>`` via
  ``hermes config set``, the same CLI path ``jarvis system free-voice`` uses)
  and returns the ``provider::model`` id the brain card selects.

Loopback only: a URL on another host is refused, so nothing here can be used
to make Jarvis call out to the network. No key is read or written.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

#: Ports a local model server usually listens on, with what usually runs there.
#: 11437/11438 are the owner's Qwen and Gemma ``llama-server`` launchers.
KNOWN_PORTS: tuple[tuple[int, str], ...] = (
    (11437, "llama.cpp"),
    (11438, "llama.cpp"),
    (11435, "llama.cpp"),
    (8080, "llama.cpp"),
    (1234, "LM Studio"),
    (8000, "vLLM"),
    (11434, "Ollama"),
)

#: How long one probe may take. Loopback answers in milliseconds; a closed
#: port refuses at once. Short, because the settings page waits on the scan.
PROBE_TIMEOUT = httpx.Timeout(1.5, connect=0.5)

_LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}  # noqa: S104 — a bind address, mapped to localhost

Runner = Callable[[list[str]], tuple[int, str]]


class NotLocal(ValueError):
    """The URL is not a server on this computer."""


@dataclass
class LocalServer:
    """One local model server as the settings page shows it."""

    url: str
    port: int
    kind: str
    #: "running" | "loading" | "off"
    state: str
    models: list[str] = field(default_factory=list)
    #: The Hermes provider whose base_url is this server, if any.
    hermes_provider: str = ""
    #: The model that provider names (``providers.<name>.model``).
    hermes_model: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def server_root(url: str) -> str:
    """``scheme://host:port`` of a pasted URL, ``/v1`` and slashes dropped.

    Raises :class:`NotLocal` for a host that is not this computer.
    """
    text = (url or "").strip()
    if not text:
        raise NotLocal("Type the server address, e.g. http://127.0.0.1:11437")
    if "://" not in text:
        text = "http://" + text
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    if host not in _LOOPBACK:
        raise NotLocal("Only a server on this computer can be added here (127.0.0.1 or localhost)")
    if parts.scheme not in ("http", "https"):
        raise NotLocal("The address must start with http://")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    shown = "127.0.0.1" if host in ("0.0.0.0", "localhost") else host  # noqa: S104 — bind address
    if ":" in shown:
        shown = f"[{shown}]"
    return f"{parts.scheme}://{shown}:{port}"


def _port_of(root: str) -> int:
    return urlsplit(root).port or 80


def _kind_for(port: int, owned_by: Iterable[str], default: str) -> str:
    owners = {o.lower() for o in owned_by}
    if "llamacpp" in owners:
        return "llama.cpp"
    if "library" in owners or port == 11434:
        return "Ollama"
    return default


async def probe(client: httpx.AsyncClient, root: str, default_kind: str = "") -> LocalServer:
    """Ask one server for its models. Never raises."""
    port = _port_of(root)
    fallback = default_kind or "OpenAI-compatible"
    try:
        resp = await client.get(root + "/v1/models", timeout=PROBE_TIMEOUT)
    except httpx.HTTPError:  # nothing listening: reported as state 'off', which the page shows
        return LocalServer(url=root, port=port, kind=fallback, state="off")
    if resp.status_code == 503:
        # llama-server answers 503 while it is still loading the model.
        return LocalServer(url=root, port=port, kind=fallback, state="loading")
    if resp.status_code >= 400:
        return LocalServer(url=root, port=port, kind=fallback, state="off")
    try:
        body = resp.json()
    except ValueError:  # not an OpenAI-style answer: reported as state 'off'
        return LocalServer(url=root, port=port, kind=fallback, state="off")
    rows = body.get("data") if isinstance(body, dict) else None
    rows = rows if isinstance(rows, list) else []
    models = [str(r.get("id")) for r in rows if isinstance(r, dict) and r.get("id")]
    owners = [str(r.get("owned_by") or "") for r in rows if isinstance(r, dict)]
    return LocalServer(
        url=root,
        port=port,
        kind=_kind_for(port, owners, fallback),
        state="running",
        models=models,
    )


def hermes_local_providers(config: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """``{server root: (provider name, model)}`` for Hermes's loopback providers.

    Reads both shapes Hermes accepts: the v12+ ``providers:`` mapping and the
    legacy ``custom_providers:`` list.
    """
    found: dict[str, tuple[str, str]] = {}
    entries: list[tuple[str, dict[str, Any]]] = []
    providers = config.get("providers")
    if isinstance(providers, dict):
        entries += [(str(k), v) for k, v in providers.items() if isinstance(v, dict)]
    legacy = config.get("custom_providers")
    if isinstance(legacy, list):
        entries += [(str(e.get("name") or ""), e) for e in legacy if isinstance(e, dict)]
    for name, entry in entries:
        if not name or entry.get("enabled") is False:
            continue
        try:
            root = server_root(str(entry.get("base_url") or entry.get("url") or ""))
        except NotLocal:  # a cloud provider in Hermes's config: not a local server, skipped
            continue
        model = str(entry.get("model") or entry.get("default_model") or "")
        found.setdefault(root, (name, model))
    return found


def read_hermes_config() -> dict[str, Any]:
    from jarvis.plugins.brain.hermes_inventory import _load_config, find_hermes_home

    home = find_hermes_home()
    if home is None:
        return {}
    data, _error = _load_config(home)
    return data


async def scan(
    *,
    extra: Iterable[str] = (),
    hermes_config: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[LocalServer]:
    """Every local server worth showing: running ones, and ones Hermes names.

    A known port with nothing on it is left out; a server Hermes or Jarvis is
    configured for is always listed, so "Qwen is not running" is said, not
    hidden.
    """
    config = await asyncio.to_thread(read_hermes_config) if hermes_config is None else hermes_config
    named = hermes_local_providers(config)
    wanted: dict[str, str] = {}
    for port, kind in KNOWN_PORTS:
        wanted.setdefault(f"http://127.0.0.1:{port}", kind)
    pinned: set[str] = set(named)
    for url in extra:
        try:
            root = server_root(url)
        except NotLocal:  # a remote URL on the local card: never probed, by design
            continue
        pinned.add(root)
        wanted.setdefault(root, "")
    for root in named:
        wanted.setdefault(root, "")

    own = client is None
    http = client or httpx.AsyncClient(trust_env=False)
    try:
        found = await asyncio.gather(*(probe(http, root, kind) for root, kind in wanted.items()))
    finally:
        if own:
            await http.aclose()
    servers: list[LocalServer] = []
    for server in found:
        if server.state == "off" and server.url not in pinned:
            continue
        name, model = named.get(server.url, ("", ""))
        server.hermes_provider, server.hermes_model = name, model
        if server.kind in ("", "OpenAI-compatible") and name:
            server.kind = "llama.cpp" if "llama" in name.lower() else server.kind
        servers.append(server)
    servers.sort(key=lambda s: (s.state != "running", not s.hermes_provider, s.port))
    return servers


def provider_name_for(model: str, taken: Iterable[str] = ()) -> str:
    """A Hermes provider name for a server serving ``model``: ``local-qwen``.

    The first word of the model id (``Qwen3.5-9B-Q4_K_M.gguf`` → ``qwen``),
    numbered when that name is taken by a provider on another server.
    """
    stem = model.rsplit("/", 1)[-1].lower()
    word = re.match(r"[a-z]+", stem)
    base = f"local-{word.group(0) if word else 'model'}"
    used = set(taken)
    name, n = base, 2
    while name in used:
        name, n = f"{base}-{n}", n + 1
    return name


def _provider_names(config: dict[str, Any]) -> set[str]:
    """Every provider name Hermes's config already uses, local or not."""
    names: set[str] = set()
    providers = config.get("providers")
    if isinstance(providers, dict):
        names |= {str(k) for k in providers}
    legacy = config.get("custom_providers")
    if isinstance(legacy, list):
        names |= {str(e.get("name")) for e in legacy if isinstance(e, dict) and e.get("name")}
    return names


def hermes_provider_entry(root: str, model: str) -> dict[str, Any]:
    """The ``providers.<name>`` value for one local OpenAI-compatible server."""
    return {"base_url": root + "/v1", "model": model, "api_mode": "chat_completions"}


@dataclass
class ConnectResult:
    provider: str
    model: str
    #: What the brain card selects: ``provider::model``.
    selection: str
    #: True when Hermes's config was written (False: it already had it).
    changed: bool


def connect(
    server: LocalServer,
    *,
    model: str = "",
    hermes_config: dict[str, Any],
    hermes_argv: list[str] | None,
    run: Runner,
) -> ConnectResult:
    """Make ``server`` a Hermes provider (if it is not one) and name the selection."""
    chosen = model or server.hermes_model or (server.models[0] if server.models else "")
    if not chosen:
        raise ValueError(
            "The server lists no model yet. Start it with a model loaded, then try again."
        )
    named = hermes_local_providers(hermes_config)
    current = named.get(server.url)
    if current is not None and (current[1] == chosen or not current[1]):
        return ConnectResult(current[0], chosen, f"{current[0]}::{chosen}", changed=False)
    if not hermes_argv:
        raise RuntimeError(
            "The hermes command is not on PATH, so Hermes can't be told about this server."
        )
    name = (
        current[0]
        if current is not None
        else provider_name_for(chosen, _provider_names(hermes_config))
    )
    entry = json.dumps(hermes_provider_entry(server.url, chosen))
    code, output = run([*hermes_argv, "config", "set", f"providers.{name}", entry])
    if code != 0:
        raise RuntimeError(f"hermes config set failed: {output[:300]}")
    log.info("local server %s is now Hermes provider %s (model %s)", server.url, name, chosen)
    return ConnectResult(name, chosen, f"{name}::{chosen}", changed=True)
