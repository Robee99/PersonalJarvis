"""The Model page's "On this computer" list: local model servers, one click to use.

``GET /api/local-servers`` → the local servers found (see
:mod:`jarvis.brain.local_servers`), whether Hermes is the brain, and what the
brain card has selected now.

``POST /api/local-servers/use`` ``{url, model?}`` → use that server's model as
the brain's model. With Hermes as the brain (ADR-0042) the server becomes a
Hermes provider and the Hermes card selects ``provider::model``; Hermes still
runs the turn, its tools and its approvals. Without Hermes, the server becomes
the URL and model of Jarvis's own "Local server (OpenAI-compatible)" card.
Nothing here switches the brain provider itself.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from jarvis.brain import local_servers
from jarvis.brain.route_policy import hermes_is_main_brain

router = APIRouter(prefix="/api/local-servers", tags=["local-servers"])

LOCAL_CARD = "local-openai"
log = logging.getLogger(__name__)


def _card(cfg: Any, provider: str) -> Any:
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    return providers.get(provider) if isinstance(providers, dict) else None


def _card_value(cfg: Any, provider: str, key: str) -> str:
    return str(getattr(_card(cfg, provider), key, "") or "")


@router.get("")
async def list_local_servers(request: Request) -> dict[str, Any]:
    """The local servers worth showing, with the brain's current selection."""
    from jarvis.ui.web.provider_routes import _resolve_cfg

    cfg = _resolve_cfg(request)
    hermes = hermes_is_main_brain(cfg)
    extra = [_card_value(cfg, LOCAL_CARD, "base_url")]
    servers = await local_servers.scan(extra=[u for u in extra if u])
    if hermes:
        selection = _card_value(cfg, "hermes", "model")
    else:
        url = _card_value(cfg, LOCAL_CARD, "base_url")
        model = _card_value(cfg, LOCAL_CARD, "model")
        selection = f"{url}|{model}" if url else ""
    return {
        "hermes_brain": hermes,
        "selection": selection,
        "servers": [s.as_dict() for s in servers],
        "hermes_min_context": local_servers.HERMES_MIN_CONTEXT,
    }


class UseBody(BaseModel):
    url: str = Field(..., max_length=300)
    model: str = Field(default="", max_length=300)
    direct: bool = False


@router.post("/use")
async def use_local_server(body: UseBody, request: Request) -> dict[str, Any]:
    """Make the brain answer with this local server's model."""
    from jarvis.ui.web.provider_routes import BrainModelBody, _apply_brain_model, _resolve_cfg

    try:
        root = local_servers.server_root(body.url)
    except local_servers.NotLocal as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    cfg = _resolve_cfg(request)
    servers = await local_servers.scan(extra=[root])
    server = next((s for s in servers if s.url == root), None)
    if server is None or (server.state != "running" and not server.hermes_provider):
        raise HTTPException(
            status_code=409,
            detail=f"Nothing answers at {root}. Start the model server there, then try again.",
        )

    if hermes_is_main_brain(cfg) and not body.direct:
        if server.context is not None and server.context < local_servers.HERMES_MIN_CONTEXT:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"The server at {root} runs with a {server.context:,}-token context window; "
                    f"Hermes Agent needs at least {local_servers.HERMES_MIN_CONTEXT:,}."
                ),
            )
        from jarvis.cli_ctl.free_voice import _local_hermes_argv, _run
        from jarvis.plugins.brain.hermes_inventory import clear_cache

        hermes_config = await asyncio.to_thread(local_servers.read_hermes_config)
        try:
            result = await asyncio.to_thread(
                local_servers.connect,
                server,
                model=body.model.strip(),
                hermes_config=hermes_config,
                hermes_argv=_local_hermes_argv(),
                run=_run,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        clear_cache()
        saved = await _apply_brain_model(
            "hermes", result.selection, BrainModelBody(model=result.selection), request, probe=False
        )
        return {
            "ok": True,
            "brain": "hermes",
            "selection": result.selection,
            "hermes_provider": result.provider,
            "hermes_changed": result.changed,
            "server_state": server.state,
            "applied_live": saved.applied_live,
        }

    model = body.model.strip() or (server.models[0] if server.models else "")
    if not model:
        raise HTTPException(status_code=409, detail="The server lists no model yet.")
    from jarvis.core import config_writer

    try:
        await asyncio.to_thread(config_writer.set_provider_base_url, LOCAL_CARD, root)
    except Exception as exc:  # noqa: BLE001 — persistence failure must reach the UI with context
        log.exception("Could not save local model server URL for %s", root)
        if isinstance(exc, PermissionError):
            detail = (
                "Windows denied Jarvis access to replace its configuration file. "
                "Check that the active Jarvis config is writable and not locked, then retry."
            )
        else:
            detail = f"Could not save the local model server URL ({type(exc).__name__}): {exc}"
        raise HTTPException(status_code=500, detail=detail) from exc
    card = _card(cfg, LOCAL_CARD)
    if card is not None and hasattr(card, "base_url"):
        card.base_url = root
    saved = await _apply_brain_model(
        LOCAL_CARD, model, BrainModelBody(model=model), request, probe=False
    )
    return {
        "ok": True,
        "brain": LOCAL_CARD,
        "selection": f"{root}|{model}",
        "server_state": server.state,
        "applied_live": saved.applied_live,
    }
