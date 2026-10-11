from __future__ import annotations

import pytest

from jarvis.plugins.tool.system_volume import SystemVolumeTool, VolumeUnavailable


class _Endpoint:
    def __init__(self, level: float = 0.5, muted: bool = False) -> None:
        self.level = level
        self.muted = muted

    def GetMasterVolumeLevelScalar(self) -> float:  # noqa: N802 - COM name
        return self.level

    def GetMute(self) -> int:  # noqa: N802 - COM name
        return int(self.muted)

    def SetMasterVolumeLevelScalar(self, value: float, _ctx: object) -> None:  # noqa: N802
        self.level = value

    def SetMute(self, value: int, _ctx: object) -> None:  # noqa: N802
        self.muted = bool(value)


def _tool(endpoint: _Endpoint) -> SystemVolumeTool:
    class _Opener:
        def __enter__(self) -> _Endpoint:
            return endpoint

        def __exit__(self, *exc: object) -> None:
            return None

    return SystemVolumeTool(opener=_Opener)


async def test_set_reads_back_the_new_level_and_unmutes() -> None:
    endpoint = _Endpoint(level=1.0, muted=True)
    result = await _tool(endpoint).execute({"action": "set", "percent": 30}, None)  # type: ignore[arg-type]
    assert result.success
    assert result.output == "Volume is 30%."
    assert endpoint.muted is False


@pytest.mark.parametrize(
    ("action", "expected"), [("up", "Volume is 60%."), ("down", "Volume is 40%.")]
)
async def test_up_and_down_step_by_ten(action: str, expected: str) -> None:
    result = await _tool(_Endpoint(level=0.5)).execute({"action": action}, None)  # type: ignore[arg-type]
    assert result.output == expected


async def test_mute_and_unmute() -> None:
    endpoint = _Endpoint(level=0.5)
    muted = await _tool(endpoint).execute({"action": "mute"}, None)  # type: ignore[arg-type]
    assert muted.output == "Volume is 50%, muted."
    unmuted = await _tool(endpoint).execute({"action": "unmute"}, None)  # type: ignore[arg-type]
    assert unmuted.output == "Volume is 50%."


async def test_no_speaker_is_an_honest_error() -> None:
    class _Broken:
        def __enter__(self) -> None:
            raise VolumeUnavailable("No speaker is connected.")

        def __exit__(self, *exc: object) -> None:
            return None

    result = await SystemVolumeTool(opener=_Broken).execute({"action": "get"}, None)  # type: ignore[arg-type]
    assert not result.success
    assert result.error == "No speaker is connected."
