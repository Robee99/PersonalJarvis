"""Nous Portal — Nous Research's hosted, OpenAI-compatible model API.

Nous Portal (portal.nousresearch.com) is a cloud model host: one ``sk-nous-…``
key reaches the models Nous serves — its own Hermes family (e.g.
``Hermes-4-70B``) and partner models, including ``:free`` variants usable on a
free account (e.g. ``stepfun/step-3.7-flash:free``). It is NOT "Hermes Agent",
the separate CLI agent product that Jarvis drives as a subagent elsewhere.

The endpoint speaks the OpenAI Chat-Completions format, so this brain is a thin
binding of the shared ``_openai_base`` streamer to Nous's fixed base URL — the
same shape as the NVIDIA NIM brain. One Nous-specific rule: every chat request
must carry a user tag in the body (``{"tags": ["user=<name>"]}``) or the API
answers 400 "missing user tag", so ``complete`` always sends one.

The card's server-URL field can point the brain at an OpenAI-compatible
gateway on this machine (e.g. ``http://127.0.0.1:11436``) that signs upstream
with its own Nous login; on such a loopback URL no client key is needed. It is
still a cloud provider: the models run at Nous, so images stay off it.

Bring-your-own-key, capability-gated (AP-21): a missing key raises a clean
error, and the model id comes from the user's pick (the live ``/v1/models``
catalog).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import BrainDelta, BrainRequest

from ._openai_base import CLIENT_TIMEOUT, stream_complete

# Nous Portal's OpenAI-compatible endpoint. Passed as the vendor default so an
# explicit ``[brain.providers.nous].base_url`` override (the card's server-URL
# field) or the team proxy can still redirect it (resolve_provider_endpoint).
BASE_URL = "https://inference-api.nousresearch.com/v1"

# Sent as the SDK key when the base URL is a loopback gateway and no key is
# stored: the gateway authenticates upstream with its own login, but the SDK
# refuses an empty key. Never sent to a remote host.
LOOPBACK_PLACEHOLDER_KEY = "local-gateway"

# The user tag the Portal requires on every chat request. A fixed, non-personal
# label: it identifies the calling app, never the person using it.
USER_TAG = "user=jarvis"

# Last-resort default when the brain is built with NO model. A ``:free`` route,
# so a model-less construction works on a free account and never bills a paid
# model by surprise; the manager passes the user's pick in almost every path.
DEFAULT_MODEL = "stepfun/step-3.7-flash:free"


def user_tag_body() -> dict[str, Any]:
    """The request-body extension every Nous chat call must carry (fresh copy)."""
    return {"tags": [USER_TAG]}


def chat_base_url(base_url: str | None, *, via_proxy: bool = False) -> str:
    """The OpenAI-compatible ``…/v1`` base for a resolved endpoint.

    The server-URL field stores a bare server root (a pasted ``/v1`` is
    stripped), so the root is normalized and ``/v1`` appended — the vendor
    default and ``http://127.0.0.1:11436`` alike. A team-proxy route is
    already complete and is used as-is.
    """
    if via_proxy and base_url:
        return base_url
    from .ollama import normalize_server_root

    return normalize_server_root(base_url or BASE_URL) + "/v1"


class NousBrain:
    name: str = "nous"
    # Budget hint, not a hard cap; the real window depends on the served model.
    context_window: int = 128_000
    supports_tools: bool = True
    # Not advertised: the Portal publishes no per-model vision capability (the
    # Hermes family is text-only) and no privacy policy, so screenshots and
    # images are never sent here — the shared builder drops them with a WARN
    # and vision work stays with a provider that declares it.
    supports_vision: bool = False

    def __init__(self, model: str | None = None) -> None:
        self._model = model or DEFAULT_MODEL
        self._client: Any = None

    def can_call_tools(self) -> bool:
        return self.supports_tools

    def _ensure_client(self) -> Any:
        if self._client is None:
            ep = cfg.resolve_provider_endpoint("nous", vendor_default_base_url=BASE_URL)
            base_url = chat_base_url(ep.base_url, via_proxy=ep.via_proxy)
            credential = ep.credential
            if not credential:
                # Keyless only on loopback: a gateway on this machine signs
                # upstream itself. A remote host still needs the user's key.
                if ep.via_proxy or not cfg.is_loopback_url(base_url):
                    raise RuntimeError(
                        "No Nous Portal API key found (nous_api_key / NOUS_API_KEY). "
                        "Create one at portal.nousresearch.com (starts with sk-nous-), "
                        "or point the server URL at a local gateway on this machine."
                    )
                credential = LOOPBACK_PLACEHOLDER_KEY
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=credential,
                base_url=base_url,
                timeout=CLIENT_TIMEOUT,
            )
        return self._client

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        client = self._client
        if client is None:
            # First use imports the SDK — seconds on a cold disk, and never on
            # the event loop (BUG-189; see claude_api.py for the measurement).
            client = await asyncio.to_thread(self._ensure_client)
        async for delta in stream_complete(
            client,
            self._model,
            req,
            extra_body=user_tag_body(),
            supports_vision=self.supports_vision,
        ):
            yield delta

    def estimate_cost(self, req: BrainRequest) -> float:
        # ``:free`` routes cost nothing; other Portal prices are model-dependent,
        # so a conservative dummy estimate mirrors the NVIDIA/OpenRouter brains.
        if ":free" in self._model.lower():
            return 0.0
        in_tokens = sum(len(str(m.content)) for m in req.messages) // 4
        return (in_tokens * 5 + req.max_tokens * 15) / 1_000_000
