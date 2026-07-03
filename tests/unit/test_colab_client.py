from __future__ import annotations

import httpx
import pytest

from colab_cli.core.colab.client import ColabClient
from colab_cli.errors import AuthError, ColabRuntimeError, ConnectionError


@pytest.mark.asyncio
async def test_assign_runtime_uses_get_then_post_with_xsrf() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                text=""")]}'
{"token":"xsrf-token","variant":"GPU","acc":"T4","nbh":"hash"}""",
            )
        return httpx.Response(
            200,
            json={
                "endpoint": "endpoint-123",
                "accelerator": "T4",
                "runtimeProxyInfo": {
                    "url": "https://proxy.example.com",
                    "token": "proxy-token",
                    "tokenExpiresInSeconds": 3600,
                },
            },
        )

    client = ColabClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    try:
        runtime = await client.assign_runtime(
            access_token="access-token",
            notebook_hash="hash",
            variant="GPU",
            accelerator="T4",
            authuser=0,
        )
    finally:
        await client.aclose()

    assert runtime.endpoint == "endpoint-123"
    assert requests[0].method == "GET"
    assert requests[0].headers["Authorization"] == "Bearer access-token"
    assert requests[1].headers["X-Goog-Colab-Token"] == "xsrf-token"


@pytest.mark.asyncio
async def test_fetch_runtime_proxy_token_parses_ttl() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=""")]}'
{"token":"proxy-token","tokenTtl":"3600s","url":"https://proxy.example.com"}""",
        )

    client = ColabClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    try:
        proxy = await client.fetch_runtime_proxy_token(
            access_token="access-token",
            endpoint_id="endpoint-123",
        )
    finally:
        await client.aclose()

    assert proxy.token == "proxy-token"
    assert proxy.url == "https://proxy.example.com"
    assert proxy.token_ttl == "3600s"


@pytest.mark.asyncio
async def test_keep_alive_and_unassign_use_tunnel_headers() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "unassign" in str(request.url) and request.method == "GET":
            return httpx.Response(200, json={"token": "disconnect-xsrf"})
        return httpx.Response(200, json={"ok": True})

    client = ColabClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    try:
        await client.keep_alive(access_token="access-token", endpoint_id="endpoint-123")
        await client.unassign_runtime(access_token="access-token", endpoint_id="endpoint-123")
    finally:
        await client.aclose()

    assert requests[0].headers["X-Colab-Tunnel"] == "Google"
    assert requests[2].headers["X-Goog-Colab-Token"] == "disconnect-xsrf"


def _client_returning(status_code: int) -> ColabClient:
    return ColabClient(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(status_code))
        ),
    )


@pytest.mark.asyncio
async def test_keep_alive_maps_404_to_runtime_error() -> None:
    client = _client_returning(404)
    try:
        with pytest.raises(ColabRuntimeError):
            await client.keep_alive(access_token="t", endpoint_id="endpoint-123")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_fetch_runtime_proxy_token_maps_401_to_auth_error() -> None:
    client = _client_returning(401)
    try:
        with pytest.raises(AuthError):
            await client.fetch_runtime_proxy_token(access_token="t", endpoint_id="endpoint-123")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_assign_runtime_maps_server_error_to_connection_error() -> None:
    client = _client_returning(500)
    try:
        with pytest.raises(ConnectionError):
            await client.assign_runtime(access_token="t", notebook_hash="hash")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_assign_runtime_maps_transport_error_to_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network unreachable", request=request)

    client = ColabClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        with pytest.raises(ConnectionError):
            await client.assign_runtime(access_token="t", notebook_hash="hash")
    finally:
        await client.aclose()
