"""Jupyter REST helpers over the Colab runtime proxy."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx

from colab_cli.models import JupyterContent, JupyterSession

from colab_cli.core.colab.headers import build_colab_headers


def encode_contents_payload(data: bytes) -> dict[str, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "format": "base64",
            "content": base64.b64encode(data).decode("ascii"),
        }
    return {"format": "text", "content": text}


def decode_contents_payload(content: JupyterContent) -> bytes:
    if content.format == "base64" and isinstance(content.content, str):
        return base64.b64decode(content.content)
    if isinstance(content.content, str):
        return content.content.encode("utf-8")
    return b""


class JupyterRestClient:
    def __init__(
        self,
        *,
        base_url: str,
        access_token: str,
        proxy_token: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.proxy_token = proxy_token
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=30.0)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return build_colab_headers(
            self.access_token,
            proxy_token=self.proxy_token,
        )

    async def create_session(
        self,
        *,
        path: str,
        name: str,
        session_type: str = "notebook",
        kernel_name: str = "python3",
    ) -> JupyterSession:
        response = await self._client.post(
            f"{self.base_url}/api/sessions",
            headers=self._headers(),
            json={
                "path": path,
                "name": name,
                "type": session_type,
                "kernel": {"name": kernel_name},
            },
        )
        response.raise_for_status()
        return JupyterSession.model_validate(response.json())

    async def list_sessions(self) -> list[JupyterSession]:
        response = await self._client.get(
            f"{self.base_url}/api/sessions",
            headers=self._headers(),
        )
        response.raise_for_status()
        return [JupyterSession.model_validate(item) for item in response.json()]

    async def get_contents(self, path: str) -> JupyterContent:
        response = await self._client.get(
            f"{self.base_url}/api/contents/{path.lstrip('/')}",
            headers=self._headers(),
        )
        response.raise_for_status()
        return JupyterContent.model_validate(response.json())

    async def save_contents(self, path: str, data: bytes) -> JupyterContent:
        encoded = encode_contents_payload(data)
        response = await self._client.put(
            f"{self.base_url}/api/contents/{path.lstrip('/')}",
            headers=self._headers(),
            json={
                "type": "file",
                "format": encoded["format"],
                "content": encoded["content"],
            },
        )
        response.raise_for_status()
        return JupyterContent.model_validate(response.json())

    async def list_directory(self, path: str = "") -> list[JupyterContent]:
        content = await self.get_contents(path)
        if not isinstance(content.content, list):
            return []
        return [JupyterContent.model_validate(item) for item in content.content]

    async def upload_file(self, local_path: Path, remote_path: str) -> JupyterContent:
        return await self.save_contents(remote_path, local_path.read_bytes())

    async def download_file(self, remote_path: str, local_path: Path) -> Path:
        content = await self.get_contents(remote_path)
        local_path.write_bytes(decode_contents_payload(content))
        return local_path

