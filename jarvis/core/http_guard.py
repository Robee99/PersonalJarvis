"""Keep an https-only download https-only, redirect after redirect.

Several places fetch a URL that came from the community registry: the index
itself, a skill's ``SKILL.md``, a plugin's files. Each of them checks the URL
is ``https://`` before fetching, and each says why in a comment — the fetch
runs on the user's machine, so a plaintext or internal address would let a
published entry aim the backend at the loopback API, the router's admin page,
or a cloud metadata endpoint.

That check covers the FIRST url only. With ``follow_redirects=True`` the
server on the other end picks the next one, and a publisher who controls
``https://their-host/skill.md`` controls its ``Location`` header too. So the
guard has to travel with the request rather than stand in front of it.

Usage — one keyword argument on the client::

    async with httpx.AsyncClient(follow_redirects=True, **https_only_async()) as c:
        resp = await c.get(url)

    with httpx.Client(follow_redirects=True, **https_only()) as c:
        resp = c.get(url)

A redirect to anything but https raises :class:`InsecureRedirect`, which is an
``httpx.HTTPError`` — the exception every one of these call sites already
handles as "the download failed".
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any

import httpx

__all__ = [
    "BlockedDestination",
    "InsecureRedirect",
    "https_only",
    "https_only_async",
    "public_only_async",
]


class InsecureRedirect(httpx.HTTPError):
    """A redirect tried to leave https.

    Subclasses ``httpx.HTTPError`` on purpose: every download this guards is
    already wrapped in ``except httpx.HTTPError``, so a refused redirect
    surfaces as the ordinary "could not fetch" the caller knows how to report.
    """


def _check(response: httpx.Response) -> None:
    if not response.has_redirect_location:
        return
    location = response.headers.get("location")
    if not location:
        return
    # Resolved against the current URL: a relative Location keeps the scheme it
    # already had, and only an absolute one can change it.
    target = response.url.join(location)
    if target.scheme != "https":
        raise InsecureRedirect(f"refusing a non-https redirect from {response.url} to {target}")


async def _check_async(response: httpx.Response) -> None:
    _check(response)


def https_only() -> dict[str, Any]:
    """Client kwargs that refuse any redirect leaving https (sync client)."""
    return {"event_hooks": {"response": [_check]}}


def https_only_async() -> dict[str, Any]:
    """The same, for ``httpx.AsyncClient`` — its hooks must be awaitable."""
    return {"event_hooks": {"response": [_check_async]}}


# ---------------------------------------------------------------------------
# Public-destination guard: for a URL the USER pasted (not a registry entry),
# plain http is fine, but the fetch runs server-side, so it must never reach
# the loopback API, the LAN, or a cloud metadata endpoint. It runs as a
# transport, so every redirect hop is checked before it is sent.
# ---------------------------------------------------------------------------


class BlockedDestination(httpx.HTTPError):
    """The request targeted a non-public address or a disallowed scheme.

    An ``httpx.HTTPError`` for the same reason as :class:`InsecureRedirect`:
    call sites already report that as "the download failed".
    """


def _is_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value.split("%", 1)[0])  # drop an IPv6 zone id
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def _public_addresses(request: httpx.Request, schemes: frozenset[str]) -> list[str]:
    url = request.url
    if url.scheme not in schemes:
        raise BlockedDestination(f"refusing a {url.scheme!r} URL")
    host = url.host
    if not host:
        raise BlockedDestination("refusing a URL without a host")
    try:
        # A literal address needs no lookup.
        literal_ok = _is_public_ip(host)
    except ValueError:  # not an IP literal, so it is a hostname and DNS resolution below decides
        literal_ok = None
    if literal_ok is not None:
        if not literal_ok:
            raise BlockedDestination(f"refusing non-public address {host}")
        return [host]
    port = url.port or (443 if url.scheme == "https" else 80)
    return await _resolve_public_addresses(host, port)


def _pinned_request(request: httpx.Request, address: str) -> httpx.Request:
    # Only the transport's copy uses the pinned IP. The client's request keeps
    # its origin for cookie scoping, redirect resolution and response URLs.
    url = request.url
    headers = request.headers.copy()
    headers["host"] = url.netloc.decode("ascii")
    extensions = dict(request.extensions)
    extensions["sni_hostname"] = url.raw_host.decode("ascii")
    return httpx.Request(
        request.method,
        url.copy_with(host=address),
        headers=headers,
        stream=request.stream,
        extensions=extensions,
    )


async def _resolve_public_addresses(host: str, port: int) -> list[str]:
    """Resolve a hostname once and reject it unless every answer is public."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise BlockedDestination(f"cannot resolve {host}") from exc
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    # Every address must be public: a name that resolves to both a public and a
    # private address could otherwise be connected to on the private one.
    if not addresses or not all(_is_public_ip(str(a)) for a in addresses):
        raise BlockedDestination(f"refusing {host}: it resolves to a non-public address")
    return addresses


class _PublicOnlyTransport(httpx.AsyncBaseTransport):
    """Validate every destination and pin only the request sent to the wire."""

    def __init__(
        self, schemes: frozenset[str], transport: httpx.AsyncBaseTransport | None
    ) -> None:
        self._schemes = schemes
        # Pinned IPs may be shared by unrelated virtual hosts. Do not reuse an
        # IP-keyed TLS connection across original hostnames. This fetch path is
        # an occasional user-triggered import, so opening a socket per hop is
        # preferable to risking a connection established for another TLS name.
        self._transport = transport if transport is not None else httpx.AsyncHTTPTransport(
            # The transport honors configured certificate authorities only;
            # environment proxy discovery belongs to the outer AsyncClient.
            trust_env=True, limits=httpx.Limits(max_keepalive_connections=0)
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        addresses = await _public_addresses(request, self._schemes)
        for address in addresses[:-1]:
            try:
                return await self._transport.handle_async_request(_pinned_request(request, address))
            except (httpx.ConnectError, httpx.ConnectTimeout):
                # Connection setup failed before the request body was sent.
                # Another address from the same validated DNS snapshot may be
                # reachable (for example, IPv4 on a host without IPv6 routing).
                continue
        return await self._transport.handle_async_request(_pinned_request(request, addresses[-1]))

    async def aclose(self) -> None:
        await self._transport.aclose()


def public_only_async(
    *,
    schemes: tuple[str, ...] = ("https",),
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """``httpx.AsyncClient`` kwargs that refuse non-public destinations.

    Every request — the first and each redirect hop — must use one of
    ``schemes`` and resolve only to globally routable addresses. Hostnames are
    pinned to the validated IP only inside the transport, retaining the original
    Host, TLS SNI, redirect origin and cookie scope. Environment proxies are
    disabled because a proxy could resolve the hostname independently. Tests
    may supply a fake inner ``transport`` without opening a socket.
    """
    allowed = frozenset(s.lower() for s in schemes)

    return {"transport": _PublicOnlyTransport(allowed, transport), "trust_env": False}
