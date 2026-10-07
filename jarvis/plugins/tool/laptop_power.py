"""laptop_power tool: power mode, ASUS operating mode and battery charge limit.

"Jarvis, turbo mode" / "silent mode" / "cap the battery at 80". Three controls,
all instantly reversible:

* ``windows_mode`` - the Windows 11 power mode slider (any laptop).
* ``asus_mode`` - Armoury Crate's operating mode (balanced / turbo / silent).
* ``charge_limit`` - the ASUS battery charge limit (60-100 %).

Risk tier: monitor. Each change is audited and can be undone with one more
sentence; nothing here can overheat the machine, kill an app or reboot it
(see jarvis/platform/laptop_power.py for what is deliberately left out).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.platform.laptop_power import (
    ASUS_MODES,
    MIN_CHARGE_LIMIT,
    WINDOWS_POWER_MODES,
    AsusControl,
    LaptopPowerUnavailable,
    WindowsPowerMode,
    armoury_crate_running,
)

log = logging.getLogger(__name__)

_ACTIONS = ("status", "set_windows_mode", "set_asus_mode", "set_charge_limit")
_OVERWRITE_NOTE = (
    " Armoury Crate is running and may switch this back after sleep, a reboot "
    "or plugging in the charger."
)


class LaptopPowerTool:
    name: str = "laptop_power"
    risk_tier: str = "monitor"
    description: str = (
        "Reads or changes the laptop's power settings: the Windows power mode "
        "(efficiency / balanced / performance), the ASUS Armoury Crate operating "
        "mode (balanced / turbo / silent) and the ASUS battery charge limit "
        f"({MIN_CHARGE_LIMIT}-100 %). Use status first when unsure what is set."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "mode": {
                "type": "string",
                "enum": sorted(set(WINDOWS_POWER_MODES) | set(ASUS_MODES)),
                "description": "For set_windows_mode or set_asus_mode",
            },
            "percent": {
                "type": "integer",
                "minimum": MIN_CHARGE_LIMIT,
                "maximum": 100,
                "description": "For set_charge_limit",
            },
        },
        "required": ["action"],
    }

    def __init__(
        self,
        windows: Any | None = None,
        asus: Any | None = None,
        armoury_running: Any | None = None,
    ) -> None:
        self._windows = windows
        self._asus = asus
        self._armoury_running = armoury_running or armoury_crate_running

    def _windows_mode(self) -> WindowsPowerMode:
        return self._windows or WindowsPowerMode()

    def _asus_control(self) -> AsusControl:
        return self._asus or AsusControl()

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        action = str((args or {}).get("action") or "")
        if action not in _ACTIONS:
            return ToolResult(
                success=False,
                output=None,
                error=f"Unknown action {action!r}. Allowed: {', '.join(_ACTIONS)}",
            )
        try:
            output = await asyncio.to_thread(self._run, action, args or {})
        except (LaptopPowerUnavailable, ValueError) as exc:  # returned as the tool error
            return ToolResult(success=False, output=None, error=str(exc))
        except OSError as exc:
            log.warning("laptop_power: %s failed", action, exc_info=True)
            return ToolResult(success=False, output=None, error=f"The change failed: {exc}")
        return ToolResult(success=True, output=output)

    def _run(self, action: str, args: dict[str, Any]) -> str:
        if action == "status":
            return self._status()
        if action == "set_windows_mode":
            mode = str(args.get("mode") or "")
            self._windows_mode().set(mode)
            return f"Windows power mode set to {mode}."
        if action == "set_asus_mode":
            mode = str(args.get("mode") or "")
            self._asus_control().set_mode(mode)
            note = _OVERWRITE_NOTE if self._armoury_running() else ""
            return f"ASUS operating mode set to {mode}.{note}"
        try:
            percent = int(args.get("percent"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Give the charge limit as a whole percent.") from exc
        self._asus_control().set_charge_limit(percent)
        note = _OVERWRITE_NOTE if self._armoury_running() else ""
        return f"Battery charge limit set to {percent} %.{note}"

    def _status(self) -> str:
        parts: list[str] = []
        try:
            parts.append(f"Windows power mode: {self._windows_mode().current()}.")
        except (LaptopPowerUnavailable, OSError) as exc:  # reported in the status text
            parts.append(f"Windows power mode: unavailable ({exc}).")
        try:
            asus = self._asus_control()
            limit = asus.charge_limit()
            parts.append(
                "ASUS controls: available"
                + (f", charge limit {limit} %." if limit is not None else ".")
            )
            if self._armoury_running():
                parts.append("Armoury Crate is running and may overwrite changes.")
        except (LaptopPowerUnavailable, OSError) as exc:  # reported in the status text
            parts.append(f"ASUS controls: unavailable ({exc}).")
        return " ".join(parts)
