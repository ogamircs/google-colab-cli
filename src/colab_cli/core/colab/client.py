"""Async client for Colab tunnel and proxy APIs."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

import httpx

from colab_cli.errors import AuthError, ColabRuntimeError, ConnectionError
from colab_cli.models import AssignedRuntime, AssignHandshake, RuntimeProxyTokenResponse
from colab_cli.utils import strip_xssi_prefix

from .headers import build_colab_headers

COLAB_BASE_URL = "https://colab.research.google.com"
COLAB_PA_BASE_URL = "https://colab.pa.googleapis.com"


class ColabClient:
    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=30.0)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _send(
        self,
        method: str,
        url: str,
        *,
        runtime_scoped: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        """Issue a request, mapping failures onto the colab-cli exception hierarchy.

        runtime_scoped marks endpoints addressing an already-assigned runtime,
        where a 404/410 means the runtime was reclaimed.
        """
        try:
            response = await self._client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            raise ConnectionError(f"Could not reach Colab: {exc}") from exc
        if response.status_code in (401, 403):
            raise AuthError(
                f"Colab rejected credentials (HTTP {response.status_code}). "
                "Try `colab auth login`."
            )
        if runtime_scoped and response.status_code in (404, 410):
            raise ColabRuntimeError(
                f"Colab runtime is unavailable (HTTP {response.status_code}) — it may "
                "have been reclaimed. Run `colab connect` to allocate a new one."
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ConnectionError(
                f"Colab API error HTTP {response.status_code} for {url}"
            ) from exc
        return response

    async def assign_runtime(
        self,
        *,
        access_token: str,
        notebook_hash: str,
        variant: str | None = None,
        accelerator: str | None = None,
        authuser: int = 0,
    ) -> AssignedRuntime:
        query = _assignment_query(
            notebook_hash=notebook_hash,
            variant=variant,
            accelerator=accelerator,
            authuser=authuser,
        )
        url = f"{COLAB_BASE_URL}/tun/m/assign?{query}"
        handshake_response = await self._send(
            "GET",
            url,
            headers=build_colab_headers(access_token, tunnel=True),
        )
        handshake = AssignHandshake.model_validate(_decode_json_payload(handshake_response))

        response = await self._send(
            "POST",
            url,
            headers=build_colab_headers(
                access_token,
                tunnel=True,
                xsrf_token=handshake.token,
            ),
        )
        return AssignedRuntime.model_validate(_decode_json_payload(response))

    async def fetch_runtime_proxy_token(
        self,
        *,
        access_token: str,
        endpoint_id: str,
        port: int = 8080,
    ) -> RuntimeProxyTokenResponse:
        response = await self._send(
            "GET",
            f"{COLAB_PA_BASE_URL}/v1/runtime-proxy-token",
            runtime_scoped=True,
            params={"endpoint": endpoint_id, "port": port},
            headers=build_colab_headers(access_token),
        )
        return RuntimeProxyTokenResponse.model_validate(_decode_json_payload(response))

    async def keep_alive(
        self,
        *,
        access_token: str,
        endpoint_id: str,
        authuser: int = 0,
    ) -> None:
        await self._send(
            "GET",
            f"{COLAB_BASE_URL}/tun/m/{endpoint_id}/keep-alive/",
            runtime_scoped=True,
            params={"authuser": authuser},
            headers=build_colab_headers(access_token, tunnel=True),
        )

    async def unassign_runtime(
        self,
        *,
        access_token: str,
        endpoint_id: str,
        authuser: int = 0,
    ) -> None:
        url = f"{COLAB_BASE_URL}/tun/m/unassign/{endpoint_id}"
        response = await self._send(
            "GET",
            url,
            runtime_scoped=True,
            params={"authuser": authuser},
            headers=build_colab_headers(access_token, tunnel=True),
        )
        handshake = AssignHandshake.model_validate(_decode_json_payload(response))

        await self._send(
            "POST",
            url,
            runtime_scoped=True,
            params={"authuser": authuser},
            headers=build_colab_headers(
                access_token,
                tunnel=True,
                xsrf_token=handshake.token,
            ),
        )


def _assignment_query(
    *,
    notebook_hash: str,
    variant: str | None,
    accelerator: str | None,
    authuser: int,
) -> str:
    params = {"nbh": notebook_hash, "authuser": authuser}
    if variant:
        params["variant"] = variant
    if accelerator:
        params["accelerator"] = accelerator
    return urlencode(params)


def _decode_json_payload(response: httpx.Response) -> dict[str, object]:
    try:
        return response.json()
    except json.JSONDecodeError:
        payload = strip_xssi_prefix(response.text).strip()
        if not payload:
            return {}
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ConnectionError("Unexpected Colab API response payload") from exc
