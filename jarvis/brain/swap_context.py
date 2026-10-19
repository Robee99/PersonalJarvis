"""The local brain's context window, changed from the HUD.

The owner's model swapper (``scripts/windows/model_swap.py``) starts the main
brain (Qwen3-VL) with one context window and can restart it with another:
``POST /swap/context {"ctx": N}``, or reload it as it is with
``POST /swap/reload``. It answers with the window that is actually loaded,
which is the previous one when the new size did not load (it rolls back).

Three readers must agree on that window, or a turn is sent that the server
refuses:

* Jarvis's own local brain (``local-openai``) budgets its prompt with
  :func:`live_window`, which this module keeps from the swapper's answers.
* Hermes reads ``providers.<name>.models.<model>.context_length`` and
  ``compression.threshold_tokens`` from its ``config.yaml``; :func:`sync_hermes`
  writes both through ``hermes config set`` (the same CLI path the Model page
  uses), so the trim point stays below the new window.
* The HUD's context meter reads the swapper's status directly.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from jarvis.brain.local_servers import NotLocal, server_root

log = logging.getLogger(__name__)

#: The swapper's main-brain front; every front answers ``/swap/*`` alike.
SWAP_ROOT = "http://127.0.0.1:11440"
#: Restarting the model reads ~5 GB of weights; the swapper allows 420 s.
CHANGE_TIMEOUT_S = 450.0
#: Hermes's trim point as a share of the window, when its config names no
#: window to scale from (56,000 of 65,536 is the tested setting on the PC).
COMPRESS_SHARE = 0.85

Runner = Callable[[list[str]], tuple[int, str]]

#: Server root → the window its swapper last reported, and when.
_live: dict[str, tuple[int, float]] = {}


def remember_window(root: str, window: int) -> None:
    """Record the window the swapper at ``root`` reported."""
    if window > 0:
        _live[root] = (int(window), time.time())


def live_window(base_url: str) -> int | None:
    """The window last reported for the server at ``base_url``, if any."""
    try:
        root = server_root(base_url)
    except NotLocal:  # a remote server has no swapper of ours
        return None
    entry = _live.get(root)
    return entry[0] if entry else None


async def read_status(client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    """The swapper's ``/swap/status``, or ``None`` when no swapper answers."""
    own = client is None
    http = client or httpx.AsyncClient(trust_env=False, timeout=1.5)
    try:
        res = await http.get(f"{SWAP_ROOT}/swap/status")
        res.raise_for_status()
        status = res.json()
    except (httpx.HTTPError, ValueError):
        log.debug("swap context: no swapper status", exc_info=True)
        return None
    finally:
        if own:
            await http.aclose()
    if not isinstance(status, dict):
        return None
    window = status.get("window")
    if isinstance(window, int):
        remember_window(SWAP_ROOT, window)
    return status


async def change(ctx: int | None, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Restart the main brain with ``ctx`` tokens (``None``: reload as it is).

    Returns the swapper's answer: ``ok``, the loaded ``window`` and, when the
    new size did not load, an ``error`` naming what was kept instead.
    """
    path = "swap/reload" if ctx is None else "swap/context"
    body = {} if ctx is None else {"ctx": int(ctx)}
    own = client is None
    http = client or httpx.AsyncClient(trust_env=False, timeout=CHANGE_TIMEOUT_S)
    try:
        res = await http.post(f"{SWAP_ROOT}/{path}", json=body)
        try:
            answer = res.json()
        except ValueError:
            answer = {"ok": False, "error": res.text[:300]}
    except httpx.HTTPError as exc:
        log.warning("swap context: %s failed: %s", path, exc)
        return {"ok": False, "error": f"The model server did not answer: {exc}"}
    finally:
        if own:
            await http.aclose()
    if not isinstance(answer, dict):
        return {"ok": False, "error": "The model server sent an unreadable answer."}
    window = answer.get("window")
    if isinstance(window, int):
        remember_window(SWAP_ROOT, window)
    answer.setdefault("ok", res.status_code < 400)
    return answer


def _hermes_targets(config: dict[str, Any], model: str) -> list[tuple[str, int | None]]:
    """``(config key, current value)`` of every Hermes model entry served by the swapper."""
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return []
    found: list[tuple[str, int | None]] = []
    for name, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        address = entry.get("base_url") or entry.get("url") or entry.get("api") or ""
        try:
            if server_root(str(address)) != SWAP_ROOT:
                continue
        except NotLocal:  # a cloud provider: never the swapper
            continue
        models = entry.get("models")
        if not isinstance(models, dict):
            continue
        for model_id, spec in models.items():
            if model and str(model_id) != model:
                continue
            current = spec.get("context_length") if isinstance(spec, dict) else None
            found.append(
                (
                    f"providers.{name}.models.{model_id}.context_length",
                    current if isinstance(current, int) else None,
                )
            )
    return found


def sync_hermes(
    window: int,
    model: str,
    *,
    hermes_config: dict[str, Any],
    hermes_argv: list[str] | None,
    run: Runner,
) -> list[str]:
    """Point Hermes's window and trim point at ``window``; returns the keys changed."""
    targets = _hermes_targets(hermes_config, model)
    wanted: list[tuple[str, int]] = [(key, window) for key, current in targets if current != window]
    compression = hermes_config.get("compression")
    if isinstance(compression, dict) and isinstance(compression.get("threshold_tokens"), int):
        # Keep the owner's trim share: 56,000 of 65,536 becomes 28,000 of 32,768.
        old_window = next((current for _, current in targets if current), None)
        share = compression["threshold_tokens"] / old_window if old_window else COMPRESS_SHARE
        trim = int(window * min(share, 1.0))
        if compression["threshold_tokens"] != trim:
            wanted.append(("compression.threshold_tokens", trim))
    if not wanted:
        return []
    if not hermes_argv:
        raise RuntimeError("The hermes command is not on PATH, so Hermes keeps its old window.")
    changed: list[str] = []
    for key, value in wanted:
        code, output = run([*hermes_argv, "config", "set", key, str(value)])
        if code != 0:
            raise RuntimeError(f"hermes config set {key} failed: {output[:300]}")
        changed.append(key)
    log.info("swap context: Hermes now uses a %s-token window (%s)", window, ", ".join(changed))
    return changed
