"""Live inputs must be fresh, bounded images, without cross-turn retention."""
import base64
import io
import time
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import ValidationError

from jarvis.vision.live_inputs import LiveVisionFrame, decode_frames, live_image_scope


def frame(source="screen", **changes):
    image = io.BytesIO()
    Image.new("RGB", (80, 60), "blue").save(image, "JPEG")
    return LiveVisionFrame(**{
        "source": source, "mime": "image/jpeg",
        "data_b64": base64.b64encode(image.getvalue()).decode(),
        "captured_at_ms": time.time()*1000, **changes,
    })


def test_sources_are_independent_and_preserve_image_bytes():
    screen, webcam = frame(), frame("webcam")
    images = decode_frames([screen, webcam])
    assert len(images) == 2
    assert images[0].data_b64 == screen.data_b64
    assert images[1].data_b64 == webcam.data_b64
    assert "screen" in images[0].source_hash
    assert "webcam" in images[1].source_hash


@pytest.mark.parametrize("changes", [
    {"data_b64": "not base64"}, {"data_b64": base64.b64encode(b"not an image").decode()},
    {"captured_at_ms": (time.time()-60)*1000},
    {"captured_at_ms": (time.time()+60)*1000},
])
def test_invalid_or_stale_frames_are_refused(changes):
    with pytest.raises((ValueError, ValidationError)):
        decode_frames([frame(**changes)])


def test_duplicate_source_is_refused():
    with pytest.raises(ValueError, match="source"):
        decode_frames([frame(), frame()])


def test_wrong_mime_is_refused():
    with pytest.raises((ValueError, ValidationError)):
        decode_frames([frame(mime="image/png")])


def test_cancelled_turn_releases_pending_images():
    from jarvis.brain.manager import BrainManager

    manager = BrainManager.__new__(BrainManager)
    trace = uuid4()
    images = decode_frames([frame("webcam")])
    with pytest.raises(RuntimeError, match="cancel"):
        with live_image_scope(manager, trace, images):
            assert manager._pending_turn_images[trace] == images
            raise RuntimeError("cancel")
    assert trace not in manager._pending_turn_images


def test_scope_does_not_clear_another_conversation():
    from jarvis.brain.manager import BrainManager

    manager = BrainManager.__new__(BrainManager)
    first, second = uuid4(), uuid4()
    images = decode_frames([frame()])
    manager.inject_images_for_turn(first, images)
    with live_image_scope(manager, second, images):
        assert manager._pending_turn_images[first] == images
    assert first in manager._pending_turn_images
    assert second not in manager._pending_turn_images
