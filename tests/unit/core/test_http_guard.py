"""The https-only redirect guard.

The registry is auto-merged, so a published entry's URL is attacker-chosen and
so is the ``Location`` header of the host it points at. Checking the first URL
is https — which every one of these call sites does — proves nothing about the
second one.
"""

from __future__ import annotations

import asyncio
import socket
import ssl
from pathlib import Path

import certifi
import httpcore
import httpx
import pytest

from jarvis.core import http_guard
from jarvis.core.http_guard import (
    BlockedDestination,
    InsecureRedirect,
    https_only,
    https_only_async,
    public_only_async,
)


def _redirecting(target: str) -> httpx.MockTransport:
    """https://start.example redirects once, to ``target``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://start.example/skill.md":
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, text="landed")

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1:8765/api/anything",  # the user's own Jarvis
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://192.168.1.1/admin",  # the router on the LAN
    ],
)
@pytest.mark.asyncio
async def test_redirect_off_https_is_refused(target: str) -> None:
    async with httpx.AsyncClient(
        transport=_redirecting(target), follow_redirects=True, **https_only_async()
    ) as client:
        with pytest.raises(InsecureRedirect):
            await client.get("https://start.example/skill.md")


@pytest.mark.asyncio
async def test_refusal_is_an_http_error() -> None:
    """Every caller already handles httpx.HTTPError as "the download failed"."""
    async with httpx.AsyncClient(
        transport=_redirecting("http://127.0.0.1:8765/"),
        follow_redirects=True,
        **https_only_async(),
    ) as client:
        with pytest.raises(httpx.HTTPError):
            await client.get("https://start.example/skill.md")


@pytest.mark.asyncio
async def test_https_redirect_still_follows() -> None:
    """A CDN moving a file is the normal case and must keep working."""
    async with httpx.AsyncClient(
        transport=_redirecting("https://cdn.example/skill.md"),
        follow_redirects=True,
        **https_only_async(),
    ) as client:
        resp = await client.get("https://start.example/skill.md")
    assert resp.status_code == 200
    assert resp.text == "landed"


def test_sync_client_guard_refuses_too() -> None:
    with httpx.Client(
        transport=_redirecting("http://127.0.0.1:8765/"),
        follow_redirects=True,
        **https_only(),
    ) as client:
        with pytest.raises(InsecureRedirect):
            client.get("https://start.example/skill.md")


# --------------------------------------------------------------------------
# public_only_async — a user-pasted URL may be http, but never non-public
# --------------------------------------------------------------------------


def _public_redirecting(target: str) -> httpx.MockTransport:
    """http://8.8.8.8 (a literal public address, no DNS) redirects to target."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "8.8.8.8":
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, text="landed")

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765/api/anything",
        "http://localhost:8765/api/anything",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/admin",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
    ],
)
@pytest.mark.asyncio
async def test_public_only_refuses_non_public_first_hop(url: str) -> None:
    async with httpx.AsyncClient(
        **public_only_async(
            schemes=("http", "https"), transport=_public_redirecting("https://1.1.1.1/x")
        ),
    ) as client:
        with pytest.raises(BlockedDestination):
            await client.get(url)


@pytest.mark.parametrize("target", ["http://10.0.0.5/secret", "https://[::1]/", "https://[fd00::1]/"])
@pytest.mark.asyncio
async def test_public_only_refuses_a_redirect_into_the_lan(target: str) -> None:
    async with httpx.AsyncClient(
        follow_redirects=True,
        **public_only_async(
            schemes=("http", "https"), transport=_public_redirecting(target)
        ),
    ) as client:
        with pytest.raises(httpx.HTTPError):
            await client.get("http://8.8.8.8/skill.md")


@pytest.mark.asyncio
async def test_public_only_follows_a_public_redirect() -> None:
    async with httpx.AsyncClient(
        follow_redirects=True,
        **public_only_async(
            schemes=("http", "https"), transport=_public_redirecting("https://1.1.1.1/skill.md")
        ),
    ) as client:
        resp = await client.get("http://8.8.8.8/skill.md")
    assert resp.text == "landed"


@pytest.mark.asyncio
async def test_public_only_default_is_https_only() -> None:
    async with httpx.AsyncClient(
        **public_only_async(transport=_public_redirecting("https://1.1.1.1/x"))
    ) as client:
        with pytest.raises(BlockedDestination):
            await client.get("http://8.8.8.8/skill.md")


@pytest.mark.parametrize(
    ("url", "address", "host_header", "sni", "port"),
    [
        (
            "https://rebinding.example/resource", "93.184.216.34",
            "rebinding.example", "rebinding.example", 443,
        ),
        (
            "https://rebinding.example:8443/resource", "2606:4700:4700::1111",
            "rebinding.example:8443", "rebinding.example", 8443,
        ),
        (
            "https://bücher.example/resource", "93.184.216.34",  # i18n-allow: IDNA test host
            "xn--bcher-kva.example", "xn--bcher-kva.example", 443,
        ),
    ],
)
@pytest.mark.asyncio
async def test_public_only_pins_dns_result_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    address: str,
    host_header: str,
    sni: str,
    port: int,
) -> None:
    """The address checked by the guard is the one the transport must use."""
    resolutions: list[tuple[str, int]] = []

    async def resolve(host: str, port: int) -> list[str]:
        resolutions.append((host, port))
        # A later DNS answer would be private; connection must stay pinned to
        # the public address selected by this first lookup.
        return [address]

    monkeypatch.setattr(http_guard, "_resolve_public_addresses", resolve)
    seen: list[tuple[str, str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.url.host,
                request.headers.get("host", ""),
                request.extensions.get("sni_hostname"),
            )
        )
        return httpx.Response(200, text="ok")

    async with httpx.AsyncClient(
        **public_only_async(schemes=("http", "https"), transport=httpx.MockTransport(respond)),
    ) as client:
        response = await client.get(url)

    assert response.text == "ok"
    assert seen == [(address, host_header, sni)]
    assert resolutions == [(httpx.URL(url).host, port)]


@pytest.mark.asyncio
async def test_public_only_preserves_origin_and_cookies_across_relative_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolve(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(http_guard, "_resolve_public_addresses", resolve)
    seen: list[tuple[str, str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers["host"], request.headers.get("cookie", "")))
        if request.url.path == "/start":
            return httpx.Response(
                302, headers={"location": "/end", "set-cookie": "session=synthetic; Path=/"}
            )
        return httpx.Response(200, text="ok")

    async with httpx.AsyncClient(
        follow_redirects=True,
        **public_only_async(transport=httpx.MockTransport(respond)),
    ) as client:
        response = await client.get("https://origin.example/start")
        assert str(response.url) == "https://origin.example/end"
        assert str(response.history[0].url) == "https://origin.example/start"
        assert client.cookies.get("session", domain="origin.example") == "synthetic"
    assert seen == [
        ("93.184.216.34", "origin.example", ""),
        ("93.184.216.34", "origin.example", "session=synthetic"),
    ]


@pytest.mark.parametrize(
    "addresses",
    [[], ["93.184.216.34", "127.0.0.1"], ["2606:4700:4700::1111", "fd00::1"]],
)
@pytest.mark.asyncio
async def test_public_only_rejects_empty_or_mixed_dns_answers_before_sending(
    monkeypatch: pytest.MonkeyPatch, addresses: list[str]
) -> None:
    async def getaddrinfo(*args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(
        **public_only_async(transport=httpx.MockTransport(respond))
    ) as client:
        with pytest.raises(BlockedDestination):
            await client.get("https://mixed.example/")
    assert sent == []


@pytest.mark.asyncio
async def test_public_only_rechecks_dns_on_a_relative_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter(["93.184.216.34", "127.0.0.1"])

    async def getaddrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), 443))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)
    sent: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.host)
        return httpx.Response(302, headers={"location": "/next"})

    async with httpx.AsyncClient(
        follow_redirects=True, **public_only_async(transport=httpx.MockTransport(respond))
    ) as client:
        with pytest.raises(BlockedDestination):
            await client.get("https://rebinding.example/start")
    assert sent == ["93.184.216.34"]


@pytest.mark.parametrize("custom_ca", [False, True])
@pytest.mark.asyncio
async def test_public_only_wire_pins_ip_and_keeps_tls_connections_separate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, custom_ca: bool,
) -> None:
    """Exercise the real HTTPX/httpcore transport with only the socket layer faked."""
    connections: list[tuple[str, int]] = []
    tls_names: list[str] = []
    writes: list[bytes] = []

    class Stream(httpcore.AsyncMockStream):
        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):  # noqa: ASYNC109
            assert isinstance(server_hostname, str)
            assert ssl_context.check_hostname is True
            assert ssl_context.verify_mode == ssl.CERT_REQUIRED
            tls_names.append(server_hostname)
            return self

        async def write(self, buffer, timeout=None):  # noqa: ASYNC109 - httpcore interface
            writes.append(buffer)

    class Backend(httpcore.AsyncMockBackend):
        async def connect_tcp(self, host, port, **kwargs):
            connections.append((host, port))
            return Stream([b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"])

    async def resolve(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(http_guard, "_resolve_public_addresses", resolve)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    if custom_ca:
        bundle = await asyncio.to_thread(Path(certifi.where()).read_text, encoding="ascii")
        body = bundle.split("-----BEGIN CERTIFICATE-----", 1)[1]
        certificate = (
            "-----BEGIN CERTIFICATE-----"
            + body.split("-----END CERTIFICATE-----", 1)[0]
            + "-----END CERTIFICATE-----\n"
        )
        ca_file = tmp_path / "configured-ca.pem"
        await asyncio.to_thread(ca_file.write_text, certificate, encoding="ascii")
        monkeypatch.setenv("SSL_CERT_FILE", str(ca_file))
    options = public_only_async()
    transport = options["transport"]
    if custom_ca:
        assert transport._transport._pool._ssl_context.get_ca_certs(binary_form=True) == [
            ssl.PEM_cert_to_DER_cert(certificate)
        ]
    # Keep the production connection pool; replace only its network backend.
    transport._transport._pool._network_backend = Backend([])
    async with httpx.AsyncClient(**options) as client:
        for host in ("first.example", "second.example"):
            response = await client.get(f"https://{host}/")
            assert response.text == "ok"
            assert response.url.host == host
    assert connections == [("93.184.216.34", 443), ("93.184.216.34", 443)]
    assert tls_names == ["first.example", "second.example"]
    assert b"host: first.example\r\n" in b"".join(writes).lower()
    assert b"host: second.example\r\n" in b"".join(writes).lower()


@pytest.mark.asyncio
async def test_public_only_can_fall_back_to_another_validated_dns_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolve(host: str, port: int) -> list[str]:
        return ["2606:4700:4700::1111", "93.184.216.34"]

    attempted: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        attempted.append(request.url.host)
        if ":" in request.url.host:
            raise httpx.ConnectError("synthetic host has no IPv6 route", request=request)
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(http_guard, "_resolve_public_addresses", resolve)
    async with httpx.AsyncClient(
        **public_only_async(transport=httpx.MockTransport(respond))
    ) as client:
        response = await client.get("https://dual-stack.example/")
    assert response.text == "ok"
    assert attempted == ["2606:4700:4700::1111", "93.184.216.34"]


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.WriteError, None])
@pytest.mark.asyncio
async def test_public_only_never_retries_after_connection_setup(
    monkeypatch: pytest.MonkeyPatch, error_type,
) -> None:
    async def resolve(host: str, port: int) -> list[str]:
        return ["93.184.216.34", "1.1.1.1"]

    attempted: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        attempted.append(request.url.host)
        if error_type is not None:
            raise error_type("synthetic I/O failure", request=request)
        return httpx.Response(503)

    monkeypatch.setattr(http_guard, "_resolve_public_addresses", resolve)
    async with httpx.AsyncClient(
        **public_only_async(transport=httpx.MockTransport(respond))
    ) as client:
        if error_type is not None:
            with pytest.raises(error_type):
                await client.get("https://no-replay.example/")
        else:
            assert (await client.get("https://no-replay.example/")).status_code == 503
    assert attempted == ["93.184.216.34"]
