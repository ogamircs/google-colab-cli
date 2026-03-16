from __future__ import annotations

import httpx
import pytest

from colab_cli.core.colab.client import ColabClient


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
