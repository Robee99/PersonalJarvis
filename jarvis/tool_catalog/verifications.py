"""Read-only access to the tool verification file.

``DATA_DIR/tool-catalog/verifications.json`` (beside the live ``mcp.json``)
records the last REAL tool call made against each binding, written by whatever
ran the check (never by the catalog)::

    {"version": 1,
     "items": {"jarvis:windows-mcp": {"checked_at": "2026-10-12T09:00:00Z",
                                      "ok": true,
                                      "action": "Snapshot of the desktop",
                                      "latency_ms": 840,
                                      "tool_count": 18,
                                      "error": null}}}

Keys are ``<runtime>:<local id>`` (``jarvis:windows-mcp``, ``hermes:context7``)
or ``<kind>:<local id>`` (``skill:playwright-cli``). A missing, unreadable or
malformed file means nothing is verified; a malformed entry is skipped alone.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
#: Characters of a stored error kept (it is shown, never interpreted).
ERROR_CAP = 300
ACTION_CAP = 200
#: Largest file read; anything bigger is not a verification log.
MAX_BYTES = 2_000_000


def verifications_path() -> Path:
    from jarvis.core.config import DATA_DIR

    return Path(DATA_DIR) / "tool-catalog" / "verifications.json"


def _clean_text(value: Any, cap: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    from jarvis.core.redact import redact_secrets

    return redact_secrets(value.strip())[:cap]


def _clean_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value >= 0 else None


def _clean_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    ok, checked_at = raw.get("ok"), raw.get("checked_at")
    if not isinstance(ok, bool) or not isinstance(checked_at, str) or not checked_at.strip():
        return None
    return {
        "checked_at": checked_at.strip()[:40],
        "ok": ok,
        "action": _clean_text(raw.get("action"), ACTION_CAP),
        "latency_ms": _clean_int(raw.get("latency_ms")),
        "tool_count": _clean_int(raw.get("tool_count")),
        "error": _clean_text(raw.get("error"), ERROR_CAP),
    }


def load_verifications(
    path: Path | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """``(entries by key, file status)``; never raises.

    The status says whether the file was found and read, how many entries it
    gave and, when it could not be used, why.
    """
    target = path if path is not None else verifications_path()
    status: dict[str, Any] = {"found": False, "loaded": False, "entries": 0, "error": None}
    try:
        if not target.is_file():
            return {}, status
        status["found"] = True
        if target.stat().st_size > MAX_BYTES:
            status["error"] = "verification file is too large"
            return {}, status
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        log.warning("tool catalog: verification file unreadable: %s", type(exc).__name__)
        status["error"] = "verification file could not be read"
        return {}, status
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        status["error"] = "verification file has an unknown version"
        return {}, status
    items = data.get("items")
    if not isinstance(items, dict):
        status["error"] = "verification file has no items"
        return {}, status
    entries: dict[str, dict[str, Any]] = {}
    for key, raw in items.items():
        clean = _clean_entry(raw)
        if isinstance(key, str) and key.strip() and clean is not None:
            entries[key.strip().lower()] = clean
    status["loaded"] = True
    status["entries"] = len(entries)
    return entries, status
