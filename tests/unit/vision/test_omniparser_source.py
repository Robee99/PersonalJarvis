from __future__ import annotations

import httpx
import pytest

from jarvis.core.protocols import Observation, UIANode
from jarvis.vision.engine import VisionEngine
from jarvis.vision.omniparser_source import (
    OmniParserSource,
    nodes_from_response,
    validate_local_endpoint,
)


def test_omniparser_icon_boxes_map_to_absolute_screen_coordinates() -> None:
    payload = {
        "parsed_content_list": [
            {"type": "icon", "content": "Settings", "bbox": [0.1, 0.2, 0.4, 0.5]},
            {"type": "text", "content": "Settings", "bbox": [0.1, 0.2, 0.4, 0.5]},
        ]
    }

    nodes = nodes_from_response(
        payload,
        image_size=(1280, 720),
        monitor_geom=(-1280, 0, 1280, 720),
    )

    assert len(nodes) == 1
    assert nodes[0].name == "Settings"
    assert nodes[0].role == "Button"
    assert nodes[0].bounds == (-1152, 144, 384, 216)
    assert nodes[0].automation_id == "omniparser:1"


def test_omniparser_discards_malformed_and_explicitly_noninteractive_boxes() -> None:
    nodes = nodes_from_response(
        {
            "parsed_content_list": [
                {"type": "icon", "content": "offscreen", "bbox": [1.2, 0, 1.4, 0.2]},
                {
                    "type": "icon",
                    "content": "disabled",
                    "interactivity": False,
                    "bbox": [0, 0, 0.2, 0.2],
                },
                {"type": "icon", "content": "bad", "bbox": [0, 0, "x", 1]},
            ]
        },
        image_size=(100, 100),
        monitor_geom=(0, 0, 100, 100),
    )

    assert nodes == ()


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com/parse/",
        "http://127.0.0.1.evil.test:8000/parse/",
        "http://user:pass@127.0.0.1:8000/parse/",
    ],
)
def test_omniparser_endpoint_must_be_plain_loopback_http(endpoint: str) -> None:
    with pytest.raises(ValueError):
        validate_local_endpoint(endpoint)


def test_omniparser_endpoint_accepts_loopback_service() -> None:
    assert validate_local_endpoint("http://127.0.0.1:8000/parse/") == (
        "http://127.0.0.1:8000/parse/"
    )


@pytest.mark.asyncio
async def test_omniparser_source_posts_bounded_png_and_returns_grounded_nodes(
    tmp_path,
) -> None:
    image = bytearray(24)
    image[:8] = b"\x89PNG\r\n\x1a\n"
    image[12:16] = b"IHDR"
    image[16:24] = (200).to_bytes(4, "big") + (100).to_bytes(4, "big")
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(image)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "parsed_content_list": [
                    {"type": "icon", "content": "Close", "bbox": [0.8, 0.1, 0.9, 0.2]}
                ]
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    source = OmniParserSource("http://127.0.0.1:8000/parse/", client=http)
    try:
        nodes = await source.observe(str(screenshot), (100, 50, 200, 100))
    finally:
        await source.close()
        await http.aclose()

    assert len(requests) == 1
    assert requests[0].url.path == "/parse/"
    assert "base64_image" in requests[0].read().decode("utf-8")
    assert nodes[0].bounds == (260, 60, 20, 10)


@pytest.mark.asyncio
async def test_vision_engine_uses_optional_parser_only_when_native_nodes_are_empty() -> None:
    grounded = UIANode(role="Button", name="Close", bounds=(10, 20, 30, 40))

    class FakeOmniParser:
        calls = 0

        async def observe(self, screenshot_path, monitor_geom, *, cancel_token=None):
            self.calls += 1
            return (grounded,)

        async def close(self):
            return None

    parser = FakeOmniParser()
    engine = VisionEngine(omniparser_source=parser)
    observation = Observation(
        trace_id=__import__("uuid").uuid4(),
        timestamp_ns=1,
        screenshot_path="screen.png",
        screenshot_hash="hash",
        nodes=(),
        source="screenshot_only",
        monitor_geom=(0, 0, 100, 100),
    )
    try:
        augmented = await engine._add_visual_grounding(observation, cancel_token=None)
        unchanged = await engine._add_visual_grounding(
            Observation(
                trace_id=observation.trace_id,
                timestamp_ns=1,
                screenshot_path="screen.png",
                screenshot_hash="hash-2",
                nodes=(grounded,),
                monitor_geom=(0, 0, 100, 100),
            ),
            cancel_token=None,
        )
    finally:
        await engine.close()

    assert augmented.nodes == (grounded,)
    assert augmented.source == "full"
    assert augmented.pruning_stats["omniparser_nodes"] == 1
    assert unchanged.nodes == (grounded,)
    assert parser.calls == 1
