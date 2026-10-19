"""The SDK, local adapter, dispatcher and voice watchdog share real SSE progress."""

from __future__ import annotations

import asyncio
import json

import httpx
from openai import AsyncOpenAI

from jarvis.brain.dispatcher import BrainDispatcher
from jarvis.plugins.brain.local_openai import LocalOpenAIBrain
from jarvis.speech.pipeline import SpeechPipeline


class PrefillStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        for processed in (512, 1024, 1536, 2048, 2560, 3072):
            await asyncio.sleep(0.08)
            event = {
                "id": "acceptance",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "qwen3-vl-8b",
                "choices": [],
                "prompt_progress": {"total": 3072, "processed": processed},
            }
            yield ("data: " + json.dumps(event) + "\n\n").encode()
        event = {
            "id": "acceptance",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "qwen3-vl-8b",
            "choices": [
                {"index": 0, "delta": {"content": "VOICE_TEXT_REPAIR_OK"}, "finish_reason": "stop"}
            ],
        }
        yield ("data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n").encode()


async def test_sdk_prefill_events_keep_the_voice_turn_alive():
    requests = []

    def server(request):
        requests.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "qwen3-vl-8b",
                            "object": "model",
                            "created": 0,
                            "owned_by": "llamacpp",
                        }
                    ]
                },
            )
        assert json.loads(request.content)["return_progress"] is True
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=PrefillStream()
        )

    async with AsyncOpenAI(
        api_key="local-test-only",
        base_url="http://127.0.0.1:1/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(server)),
    ) as client:
        brain = LocalOpenAIBrain("qwen3-vl-8b")
        brain._client = client
        # Exclude the SDK's cold lazy imports/catalog request from the scaled
        # 200 ms watchdog: this test measures SSE liveness during prefill.
        _ = client.chat.completions
        assert await brain._supports_prompt_progress(client, "qwen3-vl-8b")
        dispatcher = BrainDispatcher(brain)
        pipeline = SpeechPipeline.__new__(SpeechPipeline)
        pipeline._brain_timeout_s = 0.2
        pipeline._brain_hard_timeout_s = 3.0
        pipeline._brain_stall_poll_s = 0.01

        async def answer():
            result = await dispatcher.dispatch("hello", on_progress=pipeline._mark_brain_progress)
            return result.text, False

        result = await pipeline._run_brain_with_stall_guard(answer())
    assert result == ("VOICE_TEXT_REPAIR_OK", False)
    assert [r.method for r in requests] == ["GET", "POST"]
