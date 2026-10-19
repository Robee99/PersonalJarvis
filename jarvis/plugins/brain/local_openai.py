"""Generic local OpenAI-compatible brain (HuggingFace transformers serve & co).

One card for every self-hosted server that speaks the OpenAI Chat-Completions
format: HuggingFace ``transformers serve`` (port 8000), llama.cpp
``llama-server`` (port 8080), vLLM, LM Studio (port 1234) — and TGI installs
keep working, though TGI itself is in maintenance mode and never the
recommendation. Ollama has its own dedicated provider; this is the
bring-your-own-server slot.

Unlike the cloud brains there is NO vendor default endpoint — a base URL is
REQUIRED and its absence raises an honest, example-bearing error instead of
guessing a port. The credential is OPTIONAL (``local_openai_api_key`` /
``LOCAL_OPENAI_API_KEY``): most local servers ignore it, some (LM Studio
behind a reverse proxy, vLLM with ``--api-key``) check it. Keyless by design
(§3 / AP-22): ``local-openai`` stays ABSENT from
``PROVIDER_SECRET_CANDIDATES`` so the key-aware resolver treats it as always
reachable and a dead server just fast-fails across families.

Vision defaults to UNSUPPORTED and is only ever raised by evidence: whether a
given local server accepts image content is unknowable in general, so the
shared streamer DROPS images with a warning rather than letting a text-only
server reject the whole turn. When the server's own ``/v1/models`` DOES declare
image input for the selected model (some OpenAI-compatible servers publish the
modality fields), the cached catalog carries that declaration and this brain
believes it. Unknown stays blind — a confident description of a picture nobody
looked at is the failure mode worth being conservative about.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from jarvis.core import config as cfg
from jarvis.core.protocols import BrainDelta, BrainRequest

from ._openai_base import bounded_retry_client, stream_complete
from .ollama import normalize_server_root

log = logging.getLogger(__name__)

# Same shape as the Ollama timeout: a dead local server must fail fast so the
# fallback chain crosses families; a slow CPU-bound generation may stream long.
CLIENT_TIMEOUT = httpx.Timeout(connect=2.0, read=120.0, write=30.0, pool=30.0)

_NO_BASE_URL_HELP = (
    "The local OpenAI-compatible provider needs a server URL — set it on the "
    'provider card (or [brain.providers."local-openai"].base_url). Examples: '
    "HuggingFace transformers serve: http://localhost:8000 - "
    "llama.cpp llama-server: http://localhost:8080 - "
    "LM Studio: http://localhost:1234 - vLLM: http://localhost:8000."
)


def _declared_vision_support(model: str) -> bool:
    """Whether the SELECTED model declares image input, from the cached catalog.

    Mirrors the Ollama card's probe but with the opposite default: Ollama can
    ask its server what each download can do, a generic OpenAI-compatible
    server usually cannot be asked at all — so silence means blind here.
    Synchronous by necessity (``resolve_vision_brain`` asks before any call) and
    therefore cache-only; it never touches the network.
    """
    if not model:
        return False
    try:
        from jarvis.brain.model_catalog import model_capabilities  # noqa: PLC0415 — lazy (AP-26)

        declared = model_capabilities("local-openai", model)["vision"]
    except Exception:  # noqa: BLE001 — a probe must never break construction
        log.debug("local-openai: vision capability probe failed — reading the model name")
        declared = None
    if declared is not None:
        return declared is True
    return _VISION_MODEL_NAME.search(model) is not None


#: Model families that are released with an image encoder and are served that
#: way by llama.cpp (``--mmproj``), LM Studio and vLLM. llama-server's
#: ``/v1/models`` declares no modalities, so without this a vision model on it
#: stayed "blind": live 2026-10-19, every Computer-Use step on qwen3-vl-8b was
#: first refused as screenshot-blind and only reached through the last-resort
#: rescue. The name is the evidence a generic server offers; a catalog that
#: declares the modality still wins.
_VISION_MODEL_NAME = re.compile(
    r"(?:^|[-_/.:\s])(?:vl|vlm|vision|llava|bakllava|pixtral|minicpm-v|moondream"
    r"|internvl|ui-venus|gemma-?[34])(?:$|[-_/.:\s\d])|qwen[\d.]*-?vl",
    re.IGNORECASE,
)


def _declared_thinking_switch(model: str) -> bool:
    """Whether the selected model's server takes ``chat_template_kwargs``.

    Cache-only like the vision probe; ``False`` when unknown, so a generic
    server is never sent a field it did not declare.
    """
    if not model:
        return False
    try:
        from jarvis.brain.model_catalog import model_thinking_switch  # noqa: PLC0415 — lazy (AP-26)

        return model_thinking_switch("local-openai", model)
    except Exception:  # noqa: BLE001 — a probe must never break construction
        log.debug("local-openai: thinking-switch probe failed — leaving thinking as served")
        return False


def thinking_extra_body(effort: str | None) -> dict[str, Any] | None:
    """``chat_template_kwargs`` for a turn's reasoning effort.

    ``None`` (no preference) sends nothing, so the server's own default
    applies. ``none``/``minimal`` switch thinking off; any real effort keeps
    it on.
    """
    if effort is None:
        return None
    return {"chat_template_kwargs": {"enable_thinking": effort not in ("none", "minimal")}}


#: JSON-Schema keywords llama.cpp's schema-to-grammar converter cannot always
#: turn into a grammar (regex features in ``pattern``, string formats, schema
#: logic). Live 2026-10-19: "Failed to initialize samplers: failed to parse
#: grammar" on a qwen3-vl-8b turn, which then left the local model entirely.
_GRAMMAR_HOSTILE_KEYS = frozenset(
    {
        "pattern",
        "patternProperties",
        "format",
        "propertyNames",
        "contentEncoding",
        "contentMediaType",
        "not",
        "if",
        "then",
        "else",
        "dependentSchemas",
        "dependentRequired",
        "unevaluatedProperties",
        "contains",
        "minContains",
        "maxContains",
        "uniqueItems",
        "examples",
    }
)


def _is_grammar_refusal(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "grammar" in text and ("parse" in text or "sampler" in text)


def _plain_schema(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: _plain_schema(value)
            for key, value in node.items()
            if key not in _GRAMMAR_HOSTILE_KEYS
        }
    if isinstance(node, list):
        return [_plain_schema(item) for item in node]
    return node


def _tool_schema(tool: dict[str, Any]) -> tuple[str, Any]:
    for key in ("input_schema", "parameters", "schema"):
        if key in tool:
            return key, tool[key]
    return "input_schema", {}


def _with_plain_schemas(req: BrainRequest) -> BrainRequest:
    """``req`` with every tool schema stripped of grammar-hostile keywords."""
    tools = []
    for tool in req.tools:
        key, schema = _tool_schema(tool)
        tools.append({**tool, key: _plain_schema(schema)})
    return dataclasses.replace(req, tools=tuple(tools))


def _with_open_schemas(req: BrainRequest) -> BrainRequest:
    """``req`` whose tools keep their names, descriptions and argument names
    but accept any value: the last resort before the local model is left."""
    tools = []
    for tool in req.tools:
        key, schema = _tool_schema(tool)
        props = schema.get("properties") if isinstance(schema, dict) else None
        open_props = {
            name: {"description": str(spec.get("description", ""))[:300]}
            if isinstance(spec, dict)
            else {}
            for name, spec in (props or {}).items()
        }
        tools.append({**tool, key: {"type": "object", "properties": open_props}})
    return dataclasses.replace(req, tools=tuple(tools))


class LocalOpenAIBrain:
    name: str = "local-openai"
    # llama.cpp's common default n_ctx is 8192. A 32k guess made the manager
    # send requests a local server could not accept (the server returned its
    # actual limit only after paying the full prompt-build cost). Installations
    # with a larger window can set [brain.providers."local-openai"].context_window.
    _configured_window: int = 8_192
    # llama.cpp's JSON-Schema grammar can reject very large aggregate tool
    # surfaces before generation starts. Keep the local OpenAI-compatible
    # surface below that parser ceiling; users can raise it for servers that
    # have been verified with a larger tool set.
    max_tools: int = 48
    supports_tools: bool = True
    supports_vision: bool = False
    # Set per selected model from the catalog: the server takes
    # ``chat_template_kwargs``, so the manager may ask for a no-think turn.
    supports_thinking_switch: bool = False

    def __init__(self, model: str | None = None) -> None:
        self._model = (model or "").strip()
        self._client: Any = None
        self._server_root: str | None = None
        self._credential: str | None = None
        self._prompt_progress: bool | None = None
        try:
            provider_cfg = cfg.load_config().brain.providers.get("local-openai")
            configured_window = getattr(provider_cfg, "context_window", None)
            if configured_window is not None:
                self.context_window = int(configured_window)
            configured_max_tools = getattr(provider_cfg, "max_tools", None)
            if configured_max_tools is not None:
                self.max_tools = max(0, int(configured_max_tools))
            if not self._model:
                # A chain entry without a model ("local-openai", None) runs the
                # card's configured model; its capabilities are that model's.
                self._model = str(getattr(provider_cfg, "model", "") or "").strip()
        except Exception:  # noqa: BLE001 — context sizing must never break startup
            log.debug("local-openai: context-window hint unavailable", exc_info=True)
        self.supports_vision = _declared_vision_support(self._model)
        self.supports_thinking_switch = _declared_thinking_switch(self._model)

    def can_call_tools(self) -> bool:
        return self.supports_tools

    @property
    def context_window(self) -> int:
        """The configured window, or the one the owner's model swapper reports.

        The HUD's context slider restarts the model with a new window; the
        swapper's answer is the server's real limit, so it wins over the card.
        """
        if self._server_root is not None:
            from jarvis.brain.swap_context import live_window  # noqa: PLC0415 - lazy (AP-26)

            live = live_window(self._server_root)
            if live:
                return live
        return self._configured_window

    @context_window.setter
    def context_window(self, value: int) -> None:
        self._configured_window = int(value)

    def _resolve_root(self) -> str:
        if self._server_root is None:
            ep = cfg.resolve_provider_endpoint("local-openai", vendor_default_base_url=None)
            if not ep.base_url:
                raise RuntimeError(_NO_BASE_URL_HELP)
            # Root convention (same as the Ollama card): the stored value is
            # the SERVER root; ``/v1`` is appended here, and a pasted ``…/v1``
            # is normalized away instead of doubling up.
            self._server_root = normalize_server_root(ep.base_url)
            # Optional key: a team-proxy token wins, else the user's optional
            # local key, else a placeholder the server ignores.
            self._credential = ep.credential or cfg.get_secret(
                "local_openai_api_key", "LOCAL_OPENAI_API_KEY"
            )
        return self._server_root

    def _ensure_client(self) -> Any:
        if self._client is None:
            root = self._resolve_root()
            self._client = bounded_retry_client(
                api_key=self._credential or "local",
                base_url=f"{root}/v1",
                timeout=CLIENT_TIMEOUT,
            )
        return self._client

    async def _resolve_model(self) -> str:
        """The configured model, else the first one the server lists (``/v1/models``)."""
        if self._model:
            return self._model
        root = self._resolve_root()
        headers = {}
        if self._credential:
            headers["Authorization"] = f"Bearer {self._credential}"
        try:
            async with httpx.AsyncClient(timeout=CLIENT_TIMEOUT) as client:
                resp = await client.get(f"{root}/v1/models", headers=headers)
                resp.raise_for_status()
                data = resp.json().get("data") or []
        except Exception as exc:
            raise RuntimeError(
                f"Local OpenAI-compatible server not reachable at {root} — is it "
                "running? (transformers serve / llama-server / LM Studio / vLLM)"
            ) from exc
        names = [str(m.get("id") or "").strip() for m in data]
        names = [n for n in names if n]
        if not names:
            raise RuntimeError(
                f"The server at {root} lists no models (/v1/models is empty) — "
                "load a model in the server, or set one explicitly on the card."
            )
        self._model = names[0]
        log.info("local-openai: no model configured — using first served: %s", self._model)
        # The capability is per model; with none configured it is only known now.
        self.supports_vision = _declared_vision_support(self._model)
        self.supports_thinking_switch = _declared_thinking_switch(self._model)
        return self._model

    async def _supports_prompt_progress(self, client: Any, model: str) -> bool:
        """Negotiate llama.cpp's prefill events once, outside the boot path.

        Only a server declaring its implementation for the selected model gets
        this extension. Other OpenAI-compatible servers keep their wire format.
        A missing catalog never prevents an ordinary completion.
        """
        if self._prompt_progress is None:
            self._prompt_progress = False
            listing = getattr(getattr(client, "models", None), "list", None)
            if callable(listing):
                try:
                    page = await asyncio.wait_for(listing(timeout=2.0), timeout=2.0)
                    self._prompt_progress = any(
                        getattr(entry, "id", None) == model
                        and getattr(entry, "owned_by", None) == "llamacpp"
                        for entry in page.data
                    )
                except Exception:  # noqa: BLE001 - capability discovery is optional
                    log.debug("local-openai: prompt-progress discovery unavailable", exc_info=True)
        return self._prompt_progress

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        client = self._client
        if client is None:
            # First use imports the SDK — seconds on a cold disk, and never on
            # the event loop (BUG-189; see claude_api.py for the measurement).
            client = await asyncio.to_thread(self._ensure_client)
        model = await self._resolve_model()
        extra_body = (
            thinking_extra_body(req.reasoning_effort) if self.supports_thinking_switch else None
        )
        if await self._supports_prompt_progress(client, model):
            extra_body = {**(extra_body or {}), "return_progress": True}
        attempts = (req, _with_plain_schemas(req), _with_open_schemas(req)) if req.tools else (req,)
        for attempt, request in enumerate(attempts):
            sent = False
            try:
                async for delta in stream_complete(
                    client,
                    model,
                    request,
                    extra_body=extra_body,
                    supports_vision=self.supports_vision,
                ):
                    sent = sent or bool(delta.content or delta.tool_call or delta.finish_reason)
                    yield delta
                return
            except Exception as exc:
                if sent or attempt == len(attempts) - 1 or not _is_grammar_refusal(exc):
                    raise
                log.warning(
                    "local-openai: the server could not build a grammar from the tool "
                    "schemas (%s); retrying with %s schemas",
                    str(exc)[:160],
                    "simpler" if attempt == 0 else "open",
                )

    def estimate_cost(self, req: BrainRequest) -> float:
        # Local inference has no per-token bill.
        return 0.0
