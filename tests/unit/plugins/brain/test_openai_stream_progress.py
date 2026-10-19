"""Provider-side work must reach the voice watchdog without exposing hidden text."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.plugins.brain._openai_base import stream_complete


def chunk(*, content=None, reasoning=None, tool_calls=None, progress=None):
    delta = SimpleNamespace(
        content=content, reasoning_content=reasoning, tool_calls=tool_calls or []
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=None)],
        usage=None,
        prompt_progress=progress,
    )


class Client:
    def __init__(self, chunks):
        self.chunks = chunks
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        async def events():
            for item in self.chunks:
                yield item

        return events()


@pytest.mark.parametrize(
    "work",
    [
        chunk(reasoning="Private reasoning must never become spoken or displayed text."),
        chunk(
            tool_calls=[
                SimpleNamespace(
                    index=0, id="t1", function=SimpleNamespace(name="lookup", arguments='{"q":')
                )
            ]
        ),
        chunk(progress={"total": 4096, "cache": 0, "processed": 512, "time_ms": 500}),
    ],
)
async def test_invisible_server_work_emits_a_progress_delta(work):
    req = BrainRequest(messages=(BrainMessage(role="user", content="hello"),))
    stream = stream_complete(Client([work, chunk(content="Actual answer")]), "local-model", req)
    first = await anext(stream)
    assert first.content is None
    assert first.tool_call is None
    rest = [d async for d in stream]
    assert "".join(d.content or "" for d in rest) == "Actual answer"


async def test_empty_transport_chunks_are_not_fabricated_progress():
    req = BrainRequest(messages=(BrainMessage(role="user", content="hello"),))
    result = [
        d async for d in stream_complete(Client([chunk(), chunk(content="Answer")]), "m", req)
    ]
    assert len(result) == 1 and result[0].content == "Answer"
