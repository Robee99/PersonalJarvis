"""The local model server: loopback only, capabilities asked, requests well formed.

The server here is a stand-in (``conftest.StandIn``, an ``httpx.MockTransport``),
not a model: these tests check what Jarvis sends and refuses, not what a model
answers.
"""

from __future__ import annotations

import base64

import httpx
import pytest

from jarvis.multimodal import backend
from jarvis.multimodal.config import from_mapping

from .conftest import StandIn, install, only_port


@pytest.mark.parametrize(
    "url", ["http://192.168.1.20:11439", "https://api.example.com/v1", "http://10.0.0.5:8080"]
)
async def test_a_remote_server_url_is_refused_without_any_request(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={})

    install(monkeypatch, handler)
    with pytest.raises(backend.BackendUnavailable, match="not on this computer"):
        await backend.find_server(from_mapping({"server_url": url}), "image")
    assert calls == []


async def test_complete_refuses_a_non_local_root_even_when_handed_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    install(monkeypatch, lambda r: calls.append(str(r.url)) or httpx.Response(200))
    server = backend.MediaServer(root="http://203.0.113.9:11439", model="m", modalities={"image"})
    with pytest.raises(backend.BackendUnavailable):
        await backend.complete(server, [backend.text_part("hi")], max_tokens=8, timeout_s=5)
    assert calls == []


async def test_the_http_client_ignores_proxies_and_redirects() -> None:
    async with backend._client() as client:
        assert client.follow_redirects is False
        assert client._trust_env is False


async def test_capabilities_from_props_and_models(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/props":
            return httpx.Response(200, json={"modalities": {"vision": True, "audio": False}})
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "omni",
                        "architecture": {"input_modalities": ["text", "image", "video"]},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        found, models = await backend.capabilities(client, "http://127.0.0.1:11439")
    assert found == {"image", "video"} and models == ["omni"]


async def test_auto_detect_picks_the_running_server_that_can_see(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text_only = StandIn({"vision": False, "audio": False}, model="qwen")
    seeing = StandIn({"vision": True, "audio": True}, model="gemma-4-E4B")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port == 11437:
            return text_only(request)
        if request.url.port == 11439:
            return seeing(request)
        raise httpx.ConnectError("refused", request=request)

    install(monkeypatch, handler)
    server = await backend.find_server(from_mapping({}), "image")
    assert server.root == "http://127.0.0.1:11439" and server.model == "gemma-4-E4B"
    assert {host.split(":")[0] for host in text_only.hosts | seeing.hosts} == {"127.0.0.1"}


async def test_nothing_that_can_see_is_an_honest_error_with_the_start_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, only_port(11437, StandIn({"vision": False}, model="qwen")))
    with pytest.raises(backend.BackendUnavailable) as caught:
        await backend.find_server(from_mapping({}), "image")
    text = str(caught.value)
    assert "No model running on this computer can see images" in text
    assert "text-only: http://127.0.0.1:11437 (qwen)" in text
    assert "--mmproj" in text and "llama-server" in text


async def test_a_configured_server_that_is_down_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, only_port(1, StandIn()))
    cfg = from_mapping({"server_url": "http://127.0.0.1:11439"})
    with pytest.raises(backend.BackendUnavailable, match="Nothing is answering at"):
        await backend.find_server(cfg, "audio")


async def test_a_configured_server_without_audio_is_refused_for_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, only_port(11439, StandIn({"vision": True, "audio": False})))
    cfg = from_mapping({"server_url": "http://localhost:11439"})
    assert (await backend.find_server(cfg, "image")).can("image")
    with pytest.raises(backend.BackendUnavailable, match="does not accept audio"):
        await backend.find_server(cfg, "audio")


async def test_server_modalities_cover_a_server_that_cannot_report_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def ollama_like(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "llava"}]})
        return httpx.Response(404)

    install(monkeypatch, ollama_like)
    cfg = from_mapping({"server_url": "http://127.0.0.1:11434", "server_modalities": ["vision"]})
    server = await backend.find_server(cfg, "image")
    assert server.model == "llava" and server.can("image")


def test_request_shape_for_image_and_audio() -> None:
    server = backend.MediaServer(root="http://127.0.0.1:11439", model="gemma", modalities=set())
    wav = b"RIFF....WAVEfmt "
    body = backend.build_request(
        server,
        [
            backend.image_part("data:image/jpeg;base64,AAAA"),
            backend.audio_part(wav),
            backend.text_part("What is this?"),
        ],
        max_tokens=64,
    )
    assert body["model"] == "gemma" and body["max_tokens"] == 64 and body["stream"] is False
    system, user = body["messages"]
    assert system["role"] == "system" and "Do not invent" in system["content"]
    image, audio, text = user["content"]
    assert image == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}
    assert audio["type"] == "input_audio" and audio["input_audio"]["format"] == "wav"
    assert base64.b64decode(audio["input_audio"]["data"]) == wav
    assert text == {"type": "text", "text": "What is this?"}


async def test_a_server_error_is_reported_with_its_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, only_port(11439, StandIn(status=400)))
    server = backend.MediaServer(root="http://127.0.0.1:11439", model="m", modalities={"image"})
    with pytest.raises(backend.BackendError, match="refused the request \\(400\\): boom context"):
        await backend.complete(server, [backend.text_part("x")], max_tokens=8, timeout_s=5)
