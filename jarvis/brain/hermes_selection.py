"""Control-plane access to the native conversation's durable model preference.

No model turn or provider probe is needed to read or change this preference.
Jarvis's per-surface saved models are display/history values, not authority.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class SelectionError(RuntimeError):
    """The native preference was not acknowledged; never claim it was saved."""


async def _selection(model: str | None) -> dict[str, Any]:
    from jarvis.plugins.brain.hermes import (
        AGENT_MODEL,
        SESSION_ID,
        HermesBrain,
        configured_base_url,
    )

    brain = HermesBrain()
    base = configured_base_url()
    url = f"{base}/api/jarvis/conversations/{SESSION_ID}/model"
    try:
        async with brain._client() as client:
            response = await client.request(
                "GET" if model is None else "POST",
                url,
                headers=brain._headers(base),
                timeout=8.0,
                **(
                    {"json": {"selection": model.strip() or AGENT_MODEL}}
                    if model is not None
                    else {}
                ),
            )
        data = response.json()
        if (
            response.status_code != 200
            or not isinstance(data, dict)
            or data.get("conversation_id") != SESSION_ID
            or not isinstance(data.get("selection"), str)
            or not data["selection"]
        ):
            raise ValueError("Native model preference was not acknowledged")
        if model is not None and (
            not data.get("persisted") or data["selection"] != (model.strip() or AGENT_MODEL)
        ):
            raise ValueError("Native model preference differs from the requested choice")
        return data
    except Exception as exc:  # noqa: BLE001 — no raw provider bodies or credentials
        log.warning("Native model selection unavailable (%s)", type(exc).__name__)
        raise SelectionError(
            "Hermes could not confirm the model selection. Run free-voice setup "
            "and restart Hermes if its conversation controls are missing."
        ) from None


async def get_selection() -> dict[str, Any]:
    return await _selection(None)


async def set_selection(model: str) -> dict[str, Any]:
    return await _selection(model)
