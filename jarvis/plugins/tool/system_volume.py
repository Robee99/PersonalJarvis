"""system_volume tool: the computer's master volume and mute.

"Jarvis, volume to 30", "turn it down", "mute". Hermes has no volume tool of
its own; asked to set the volume, the model reached for Spotify (live
2026-10-12 00:3x: "I couldn't set the volume because Spotify is not
connected"). This is the default speaker's master level through Windows Core
Audio (pycaw, already a Windows dependency for ducking), so every app follows.

Risk tier: safe. A volume change is instantly reversible and touches nothing
else; the result reports the level that was read back after the change.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)

_ACTIONS = ("get", "set", "up", "down", "mute", "unmute")
#: How far "up" / "down" move without a number.
DEFAULT_STEP = 10


#: CoInitialize on a thread whose COM apartment is already set the other way.
_RPC_E_CHANGED_MODE = -2147417850


class VolumeUnavailable(RuntimeError):
    """No default speaker could be reached."""


class _CoreAudio:
    """The default speaker's endpoint volume, opened per call on its own thread."""

    def __enter__(self) -> Any:
        if sys.platform != "win32":
            raise VolumeUnavailable("System volume control is only available on Windows.")
        import comtypes  # noqa: PLC0415 - Windows only, lazy
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # noqa: PLC0415

        try:
            comtypes.CoInitialize()
            self._initialised = True
        except OSError as exc:
            # Live 2026-10-25: "unmute" on a worker thread where another tool had
            # already started COM in the other mode raised RPC_E_CHANGED_MODE.
            # COM is usable there; it is just not ours to uninitialise.
            if getattr(exc, "winerror", None) != _RPC_E_CHANGED_MODE:
                raise
        speakers = AudioUtilities.GetSpeakers()
        if speakers is None:
            raise VolumeUnavailable("No speaker is connected.")
        endpoint = getattr(speakers, "EndpointVolume", None)
        if endpoint is not None:
            return endpoint
        from ctypes import POINTER, cast  # noqa: PLC0415

        from comtypes import CLSCTX_ALL  # noqa: PLC0415

        interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))

    def __exit__(self, *exc: object) -> None:
        if getattr(self, "_initialised", False):
            import comtypes  # noqa: PLC0415

            comtypes.CoUninitialize()


def _read(endpoint: Any) -> tuple[int, bool]:
    level = round(float(endpoint.GetMasterVolumeLevelScalar()) * 100)
    return level, bool(endpoint.GetMute())


def apply(action: str, percent: int | None, opener: Any = _CoreAudio) -> str:
    """Do ``action`` on the master volume and say the state read back after it."""
    with opener() as endpoint:
        level, muted = _read(endpoint)
        if action == "set":
            if percent is None:
                raise ValueError("Say which level, from 0 to 100.")
            target = max(0, min(100, int(percent)))
        elif action in ("up", "down"):
            step = DEFAULT_STEP if percent is None else max(1, min(100, int(percent)))
            target = max(0, min(100, level + (step if action == "up" else -step)))
        else:
            target = None
        if target is not None:
            endpoint.SetMasterVolumeLevelScalar(target / 100.0, None)
            if muted and target > 0:
                endpoint.SetMute(0, None)
        elif action == "mute":
            endpoint.SetMute(1, None)
        elif action == "unmute":
            endpoint.SetMute(0, None)
        level, muted = _read(endpoint)
    return f"Volume is {level}%{', muted' if muted else ''}."


class SystemVolumeTool:
    name: str = "system_volume"
    risk_tier: str = "safe"
    description: str = (
        "Reads or changes this computer's master volume (all apps): get, set to a "
        "percent, up or down by a step (default 10), mute, unmute. Use this for "
        "any 'volume' or 'mute' request, not a music app."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "percent": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "The level for set, or the step for up/down",
            },
        },
        "required": ["action"],
    }

    def __init__(self, opener: Any | None = None) -> None:
        self._opener = opener or _CoreAudio

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        del ctx
        action = str((args or {}).get("action") or "")
        if action not in _ACTIONS:
            return ToolResult(
                success=False,
                output=None,
                error=f"Unknown action {action!r}. Allowed: {', '.join(_ACTIONS)}",
            )
        raw = (args or {}).get("percent")
        try:
            percent = None if raw is None else int(raw)
            output = await asyncio.to_thread(apply, action, percent, self._opener)
        except (VolumeUnavailable, ValueError) as exc:
            return ToolResult(success=False, output=None, error=str(exc))
        except (OSError, ImportError) as exc:
            log.warning("system_volume: %s failed", action, exc_info=True)
            return ToolResult(success=False, output=None, error=f"The volume change failed: {exc}")
        return ToolResult(success=True, output=output)
