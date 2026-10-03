"""Wave 2: a tool that returns an image artifact feeds it back as an ImageBlock,
so a vision provider can see it on the brain's next iteration."""
from __future__ import annotations

from jarvis.brain.tool_use_loop import _images_from_artifacts
from jarvis.core.protocols import ImageBlock


def test_image_artifact_becomes_image_block() -> None:
    arts = ({"type": "image", "mime": "image/jpeg", "data": "QUJD"},)
    blocks = _images_from_artifacts(arts)
    assert len(blocks) == 1
    assert isinstance(blocks[0], ImageBlock)
    assert blocks[0].mime == "image/jpeg"
    assert blocks[0].data_b64 == "QUJD"


def test_default_mime_when_missing() -> None:
    blocks = _images_from_artifacts(({"type": "image", "data": "QUJD"},))
    assert len(blocks) == 1
    assert blocks[0].mime == "image/jpeg"


def test_text_only_artifacts_yield_no_images() -> None:
    assert _images_from_artifacts(()) == []
    assert _images_from_artifacts(None) == []
    assert _images_from_artifacts(("some text note",)) == []
    assert _images_from_artifacts(({"type": "image"},)) == []  # no data -> skip
    assert _images_from_artifacts(({"type": "text", "data": "x"},)) == []


class _ScreenshotTool:
    name = "screenshot"
    schema: dict = {}


class _ScreenshotExecutor:
    async def execute(self, tool, args, **_kw):
        from jarvis.core.protocols import ToolResult

        return ToolResult(
            success=True, output="captured",
            artifacts=({"type": "image", "mime": "image/png", "data": "QUJD"},),
        )


class _ScreenshotThenAnswerBrain:
    def __init__(self) -> None:
        self.requests: list = []

    async def complete(self, req):
        from jarvis.core.protocols import BrainDelta

        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(tool_call={"id": "c1", "name": "screenshot", "input": {}})
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="Done.")
        yield BrainDelta(finish_reason="stop")


async def _second_request_images(tool_images: bool) -> tuple[list, list[str]]:
    from jarvis.brain.tool_use_loop import ToolUseLoop

    brain = _ScreenshotThenAnswerBrain()
    loop = ToolUseLoop(
        brain, {"screenshot": _ScreenshotTool()}, _ScreenshotExecutor(),  # type: ignore[arg-type]
        tool_images=tool_images,
    )
    await loop.run([], user_utterance="take a screenshot")
    messages = brain.requests[1].messages
    images = [img for m in messages for img in (getattr(m, "images", ()) or ())]
    texts = [str(getattr(m, "content", "")) for m in messages if getattr(m, "role", "") == "user"]
    return images, texts


import pytest  # noqa: E402


@pytest.mark.asyncio
async def test_tool_screenshot_reaches_a_model_allowed_to_see_it() -> None:
    images, _texts = await _second_request_images(True)
    assert len(images) == 1


@pytest.mark.asyncio
async def test_tool_screenshot_stays_on_device_when_images_are_not_allowed() -> None:
    images, texts = await _second_request_images(False)
    assert images == []
    assert any("stays on this device" in t for t in texts)
