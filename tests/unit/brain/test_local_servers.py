"""Local model servers are found on their ports and become Hermes providers in one step."""

from __future__ import annotations

import json

import httpx
import pytest

from jarvis.brain import local_servers
from jarvis.brain.local_servers import LocalServer, NotLocal

QWEN = {"object": "list", "data": [{"id": "qwen", "object": "model", "owned_by": "llamacpp"}]}


def _client(routes: dict[str, httpx.Response | Exception]) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        key = f"{request.url.host}:{request.url.port}"
        answer = routes.get(key)
        if answer is None:
            raise httpx.ConnectError("refused", request=request)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


@pytest.mark.parametrize(
    ("pasted", "root"),
    [
        ("127.0.0.1:11437", "http://127.0.0.1:11437"),
        ("http://localhost:11437/v1/", "http://127.0.0.1:11437"),
        ("http://0.0.0.0:8080", "http://127.0.0.1:8080"),
    ],
)
def test_a_pasted_address_becomes_a_server_root(pasted: str, root: str) -> None:
    assert local_servers.server_root(pasted) == root


@pytest.mark.parametrize("pasted", ["http://192.168.1.5:8080", "https://api.example.com", ""])
def test_only_this_computer_is_accepted(pasted: str) -> None:
    with pytest.raises(NotLocal):
        local_servers.server_root(pasted)


@pytest.mark.asyncio
async def test_scan_finds_a_running_llama_server_and_names_its_hermes_provider() -> None:
    config = {
        "providers": {"local-qwen": {"base_url": "http://127.0.0.1:11437/v1", "model": "qwen"}}
    }
    client = _client({"127.0.0.1:11437": httpx.Response(200, json=QWEN)})

    servers = await local_servers.scan(hermes_config=config, client=client)

    assert [s.url for s in servers] == ["http://127.0.0.1:11437"]
    qwen = servers[0]
    assert qwen.state == "running" and qwen.kind == "llama.cpp" and qwen.models == ["qwen"]
    assert (qwen.hermes_provider, qwen.hermes_model) == ("local-qwen", "qwen")


@pytest.mark.asyncio
async def test_scan_finds_the_apps_default_local_model_port() -> None:
    client = _client({"127.0.0.1:11443": httpx.Response(200, json=QWEN)})

    servers = await local_servers.scan(hermes_config={}, client=client)

    assert [(s.port, s.kind, s.models) for s in servers] == [
        (11443, "llama.cpp", ["qwen"]),
    ]


@pytest.mark.asyncio
async def test_a_configured_server_that_is_down_is_listed_as_off_and_a_loading_one_as_loading() -> (
    None
):
    config = {
        "custom_providers": [
            {"name": "local-qwen", "base_url": "http://127.0.0.1:11437/v1", "model": "qwen"},
            {"name": "cloud", "base_url": "https://api.example.com/v1"},
        ],
    }
    client = _client({"127.0.0.1:8080": httpx.Response(503, json={"error": "Loading model"})})

    servers = {s.url: s for s in await local_servers.scan(hermes_config=config, client=client)}

    assert servers["http://127.0.0.1:11437"].state == "off"
    assert servers["http://127.0.0.1:11437"].hermes_provider == "local-qwen"
    assert servers["http://127.0.0.1:8080"].state == "loading"
    # Ports with nothing on them and nothing configured are left out.
    assert set(servers) == {"http://127.0.0.1:11437", "http://127.0.0.1:8080"}


def test_provider_names_come_from_the_model_and_never_collide() -> None:
    assert local_servers.provider_name_for("Qwen3.5-9B-Q4_K_M.gguf") == "local-qwen"
    assert local_servers.provider_name_for("qwen", taken={"local-qwen"}) == "local-qwen-2"
    assert local_servers.provider_name_for("models/gemma-4-e4b") == "local-gemma"


def _server(**kw: object) -> LocalServer:
    base = {
        "url": "http://127.0.0.1:8080",
        "port": 8080,
        "kind": "llama.cpp",
        "state": "running",
        "models": ["Qwen3.5-9B"],
    }
    base.update(kw)
    return LocalServer(**base)  # type: ignore[arg-type]


def test_connect_writes_a_hermes_provider_for_a_new_server() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return 0, ""

    result = local_servers.connect(
        _server(),
        hermes_config={"providers": {"local-qwen": {"base_url": "http://127.0.0.1:11437/v1"}}},
        hermes_argv=["hermes"],
        run=run,
    )

    assert result.selection == "local-qwen-2::Qwen3.5-9B" and result.changed
    argv = calls[0]
    assert argv[:4] == ["hermes", "config", "set", "providers.local-qwen-2"]
    assert json.loads(argv[4]) == {
        "base_url": "http://127.0.0.1:8080/v1",
        "model": "Qwen3.5-9B",
        "api_mode": "chat_completions",
    }


def test_connect_reuses_the_provider_hermes_already_has() -> None:
    def run(argv: list[str]) -> tuple[int, str]:
        raise AssertionError(f"nothing to write: {argv}")

    result = local_servers.connect(
        _server(url="http://127.0.0.1:11437", port=11437, models=["qwen"]),
        hermes_config={
            "providers": {"local-qwen": {"base_url": "http://127.0.0.1:11437/v1", "model": "qwen"}}
        },
        hermes_argv=None,
        run=run,
    )

    assert (result.selection, result.changed) == ("local-qwen::qwen", False)


def test_connect_without_hermes_on_path_says_so() -> None:
    with pytest.raises(RuntimeError, match="hermes command"):
        local_servers.connect(_server(), hermes_config={}, hermes_argv=None, run=lambda _a: (0, ""))


def test_hermes_providers_written_with_an_api_key_are_found() -> None:
    # Hermes's v12 provider blocks name the address ``api:``; local-qwen-vl on
    # 11440 used to be missing from the picker because only base_url was read.
    config = {
        "providers": {
            "local-qwen-vl": {"api": "http://127.0.0.1:11440/v1", "default_model": "qwen3-vl-8b"},
        }
    }
    found = local_servers.hermes_local_providers(config)
    assert found == {"http://127.0.0.1:11440": ("local-qwen-vl", "qwen3-vl-8b")}


def _path_client(routes: dict[str, httpx.Response]) -> httpx.AsyncClient:
    """A fake loopback where each ``host:port/path`` answers on its own."""

    def handle(request: httpx.Request) -> httpx.Response:
        answer = routes.get(f"{request.url.host}:{request.url.port}{request.url.path}")
        if answer is None:
            if f"{request.url.host}:{request.url.port}" in {k.split("/")[0] for k in routes}:
                return httpx.Response(404, json={"error": "not found"})
            raise httpx.ConnectError("refused", request=request)
        return answer

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


@pytest.mark.asyncio
async def test_scan_reports_the_context_window_llama_server_runs_with() -> None:
    venus = {"object": "list", "data": [{"id": "ui-venus-2-9b", "owned_by": "llamacpp"}]}
    client = _path_client(
        {
            "127.0.0.1:11441/v1/models": httpx.Response(200, json=venus),
            "127.0.0.1:11441/props": httpx.Response(
                200, json={"default_generation_settings": {"n_ctx": 16384}, "total_slots": 1}
            ),
            # Ollama has no /props: the size stays unknown, never guessed.
            "127.0.0.1:11434/v1/models": httpx.Response(200, json={"data": [{"id": "qwen3:8b"}]}),
        }
    )

    servers = {s.port: s for s in await local_servers.scan(hermes_config={}, client=client)}

    assert servers[11441].context == 16384
    assert servers[11441].context < local_servers.HERMES_MIN_CONTEXT
    assert servers[11434].context is None
    assert servers[11441].as_dict()["context"] == 16384


@pytest.mark.asyncio
async def test_a_server_that_is_not_running_has_no_context() -> None:
    config = {"providers": {"local-qwen": {"base_url": "http://127.0.0.1:11437/v1", "model": "q"}}}
    client = _path_client({})

    servers = await local_servers.scan(hermes_config=config, client=client)

    assert [(s.state, s.context) for s in servers] == [("off", None)]
