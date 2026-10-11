"""The local model server a media tool talks to: found, checked, and called.

The server is llama.cpp's ``llama-server`` started with a multimodal projector
(``--mmproj``), reached over its OpenAI-compatible ``/v1/chat/completions``:
an image travels as an ``image_url`` part holding a ``data:`` URL, a sound clip
as an ``input_audio`` part holding base64 WAV. Both stay on the loopback
interface; nothing here can address another host:

* a configured ``[multimodal].server_url`` must be loopback
  (``local_servers.server_root`` raises :class:`NotLocal` otherwise), and the
  tool refuses to run instead of falling back to anything else,
* auto-detection only ever probes loopback ports (``local_servers.scan``),
* the HTTP client ignores proxy environment variables and never follows a
  redirect, so a misconfigured proxy cannot carry a picture off the machine.

What a server can take is asked, not assumed: ``GET /props`` reports
``modalities`` (``vision``, ``audio``, ``video``) and newer builds also list
``architecture.input_modalities`` under ``GET /v1/models``. A server that
reports neither (Ollama, LM Studio) is used only when the owner configured it
with ``server_modalities``.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from jarvis.brain.local_servers import NotLocal, scan, server_root
from jarvis.multimodal.config import MediaConfig

log = logging.getLogger(__name__)

PROBE_TIMEOUT = httpx.Timeout(2.0, connect=0.5)
#: How long a capability answer is trusted before the server is asked again.
CAPABILITY_TTL_S = 30.0

#: The command the decision doc recommends, quoted in every "not running" error.
START_HINT = (
    "Start a local multimodal model with llama.cpp, for example: llama-server -m "
    "gemma-4-E4B-it-Q4_0.gguf --mmproj mmproj-gemma-4-E4B-it-Q8_0.gguf --port 11439 "
    "-c 8192 -ngl 99. Jarvis finds it on its own, or set server_url under [multimodal] in "
    "jarvis.toml."
)


class BackendUnavailable(RuntimeError):
    """No usable local server; ``str(exc)`` says what to start."""


class BackendError(RuntimeError):
    """The local server answered with an error; ``str(exc)`` is its reason."""


@dataclass
class MediaServer:
    root: str
    model: str
    modalities: set[str] = field(default_factory=set)

    def can(self, modality: str) -> bool:
        return modality in self.modalities


def local_root(url: str) -> str:
    """``server_root`` for a configured URL, as a :class:`BackendUnavailable` when remote."""
    try:
        return server_root(url)
    except NotLocal as exc:
        raise BackendUnavailable(
            f"[multimodal].server_url = {url!r} is not on this computer. Media tools only "
            "use a model running here (127.0.0.1 or localhost); nothing was sent."
        ) from exc


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(trust_env=False, follow_redirects=False)


async def capabilities(client: httpx.AsyncClient, root: str) -> tuple[set[str], list[str]]:
    """``({"image", "audio", "video"} subset, model ids)`` as the server reports them."""
    found: set[str] = set()
    models: list[str] = []
    try:
        resp = await client.get(root + "/v1/models", timeout=PROBE_TIMEOUT)
        if resp.status_code == 200:
            rows = resp.json().get("data") or []
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                if row.get("id"):
                    models.append(str(row["id"]))
                arch = row.get("architecture")
                if isinstance(arch, dict):
                    found |= {str(m) for m in arch.get("input_modalities") or []} & {
                        "image", "audio", "video"
                    }
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        log.debug("multimodal: %s/v1/models unreadable (%s)", root, exc)
    try:
        resp = await client.get(root + "/props", timeout=PROBE_TIMEOUT)
        if resp.status_code == 200:
            mods = resp.json().get("modalities")
            if isinstance(mods, dict):
                names = {"vision": "image", "audio": "audio", "video": "video"}
                found |= {names[key] for key, on in mods.items() if on and key in names}
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        log.debug("multimodal: %s/props unreadable (%s)", root, exc)
    return found, models


_cache: dict[str, tuple[float, MediaServer]] = {}


def _reset_cache_for_tests() -> None:
    _cache.clear()


async def _describe(client: httpx.AsyncClient, root: str, cfg: MediaConfig) -> MediaServer:
    cached = _cache.get(root)
    if cached and time.monotonic() - cached[0] < CAPABILITY_TTL_S:
        return cached[1]
    modalities, models = await capabilities(client, root)
    configured = cfg.server_url and local_root(cfg.server_url) == root
    if configured and cfg.server_modalities:
        modalities |= {"image" if m == "vision" else m for m in cfg.server_modalities}
    server = MediaServer(root=root, model=cfg.model or (models[0] if models else ""),
                         modalities=modalities)
    _cache[root] = (time.monotonic(), server)
    return server


async def find_server(cfg: MediaConfig, need: str) -> MediaServer:
    """The local server that can take ``need`` (``image`` or ``audio``).

    Raises :class:`BackendUnavailable` with the reason and the command to start one.
    """
    async with _client() as client:
        if cfg.server_url:
            root = local_root(cfg.server_url)
            server = await _describe(client, root, cfg)
            if server.can(need):
                return server
            if not server.modalities and not server.model:
                raise BackendUnavailable(
                    f"Nothing is answering at {root} ([multimodal].server_url). " + START_HINT
                )
            raise BackendUnavailable(
                f"The model at {root} ({server.model or 'unknown'}) does not accept {need} "
                f"input. Start it with an --mmproj projector of a model that does. " + START_HINT
            )
        extra = [f"http://127.0.0.1:{port}" for port in cfg.extra_ports]
        try:
            servers = await scan(extra=extra, client=client)
        except Exception as exc:  # noqa: BLE001 — a failed scan is reported as "nothing found"
            log.warning("multimodal: local server scan failed", exc_info=True)
            raise BackendUnavailable(
                f"Local servers could not be scanned ({exc}). " + START_HINT
            ) from exc
        running = [s for s in servers if s.state == "running"]
        loading = [s.url for s in servers if s.state == "loading"]
        checked: list[str] = []
        for candidate in running:
            server = await _describe(client, candidate.url, cfg)
            checked.append(f"{candidate.url} ({server.model or candidate.kind})")
            if server.can(need):
                return server
    what = "see images" if need == "image" else f"take {need}"
    detail = f" Running but text-only: {', '.join(checked)}." if checked else ""
    wait = f" Still loading: {', '.join(loading)}; try again in a minute." if loading else ""
    raise BackendUnavailable(
        f"No model running on this computer can {what}.{detail}{wait} " + START_HINT
    )


def image_part(data_url: str) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": data_url}}


def audio_part(wav: bytes) -> dict[str, Any]:
    return {
        "type": "input_audio",
        "input_audio": {"data": base64.b64encode(wav).decode("ascii"), "format": "wav"},
    }


def text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


SYSTEM_PROMPT = (
    "You describe media for another assistant that cannot see or hear it. Be concrete and "
    "faithful: say what is actually there, read visible text exactly, and say plainly when "
    "something is unclear. Do not invent details."
)


def build_request(
    server: MediaServer, parts: list[dict[str, Any]], *, max_tokens: int
) -> dict[str, Any]:
    """The ``/v1/chat/completions`` body for one media question."""
    body: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": parts},
        ],
        "max_tokens": int(max_tokens),
        "temperature": 0.2,
        "stream": False,
    }
    if server.model:
        body["model"] = server.model
    return body


def _error_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:  # not JSON: the raw body is the reason, shown to the caller
        return resp.text[:300]
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)[:300]
    return str(err or body)[:300]


async def complete(
    server: MediaServer,
    parts: list[dict[str, Any]],
    *,
    max_tokens: int,
    timeout_s: float,
) -> str:
    """Ask the local server; the answer text, or :class:`BackendError` with its reason."""
    local_root(server.root)  # belt and braces: never post media to a non-local address
    body = build_request(server, parts, max_tokens=max_tokens)
    try:
        async with _client() as client:
            resp = await client.post(
                server.root + "/v1/chat/completions",
                json=body,
                timeout=httpx.Timeout(timeout_s, connect=2.0),
            )
    except httpx.TimeoutException as exc:
        raise BackendError(
            f"The local model at {server.root} did not answer within {timeout_s:.0f} s."
        ) from exc
    except httpx.HTTPError as exc:
        _cache.pop(server.root, None)
        raise BackendError(f"The local model at {server.root} could not be reached: {exc}") from exc
    if resp.status_code >= 400:
        reason = _error_text(resp)
        _cache.pop(server.root, None)
        hint = ""
        if "context" in reason.lower():
            hint = (
                " Ask for fewer frames or a shorter clip, or start llama-server with a larger -c."
            )
        raise BackendError(f"The local model refused the request ({resp.status_code}): {reason}"
                           + hint)
    try:
        choice = resp.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise BackendError("The local model sent an answer Jarvis could not read.") from exc
    text = str(choice.get("content") or "").strip()
    if not text:
        text = str(choice.get("reasoning_content") or "").strip()
    if not text:
        raise BackendError("The local model answered with nothing.")
    return text


async def ask(
    cfg: MediaConfig, need: str, parts: list[dict[str, Any]], *, max_tokens: int | None = None
) -> tuple[str, MediaServer, float]:
    """Find a server for ``need`` and ask it. ``(answer, server, seconds)``."""
    server = await find_server(cfg, need)
    started = time.monotonic()
    text = await asyncio.wait_for(
        complete(server, parts, max_tokens=max_tokens or cfg.max_tokens,
                 timeout_s=cfg.timeout_seconds),
        timeout=cfg.timeout_seconds + 5,
    )
    return text, server, time.monotonic() - started
