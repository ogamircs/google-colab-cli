from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

import httpx

from colab_cli.core.jupyter.rest import JupyterRestClient, decode_contents_payload, encode_contents_payload
from colab_cli.core.jupyter.ws import KernelMessageAccumulator, KernelWebSocketClient
from colab_cli.errors import AuthError, ColabRuntimeError, ConnectionError, ExecutionError
from colab_cli.formats.notebook import extract_code_cells
from colab_cli.models import JupyterContent


def test_encode_contents_payload_uses_text_for_utf8() -> None:
    payload = encode_contents_payload("hello world".encode("utf-8"))

    assert payload["format"] == "text"
    assert payload["content"] == "hello world"


def test_encode_contents_payload_uses_base64_for_binary() -> None:
    payload = encode_contents_payload(b"\xff\x00\x01")

    assert payload["format"] == "base64"
    assert payload["content"] == base64.b64encode(b"\xff\x00\x01").decode("ascii")


def test_decode_contents_payload_round_trips_binary() -> None:
    content = JupyterContent(
        name="data.bin",
        path="/content/data.bin",
        type="file",
        format="base64",
        content=base64.b64encode(b"\xff\x00\x01").decode("ascii"),
    )

    assert decode_contents_payload(content) == b"\xff\x00\x01"


def test_decode_contents_payload_serializes_json_content() -> None:
    notebook = {
        "cells": [{"cell_type": "code", "source": ["print('hello')\n"]}],
        "metadata": {"kernelspec": {"name": "python3"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    content = JupyterContent(
        name="notebook.ipynb",
        path="/content/notebook.ipynb",
        type="notebook",
        format="json",
        content=notebook,
    )

    assert json.loads(decode_contents_payload(content).decode("utf-8")) == notebook


@pytest.mark.asyncio
async def test_download_file_uses_remote_basename_for_directory_targets(tmp_path: Path) -> None:
    client = JupyterRestClient(
        base_url="https://proxy.example.com",
        access_token="access-token",
        proxy_token="proxy-token",
    )

    async def fake_get_contents(path: str) -> JupyterContent:
        assert path == "/content/results.csv"
        return JupyterContent(
            name="results.csv",
            path=path,
            type="file",
            format="text",
            content="value\n1\n",
        )

    client.get_contents = fake_get_contents  # type: ignore[method-assign]

    try:
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()

        result = await client.download_file("/content/results.csv", download_dir)
    finally:
        await client.aclose()

    assert result == download_dir / "results.csv"
    assert result.read_text() == "value\n1\n"


def test_kernel_message_accumulator_collects_streams_and_outputs() -> None:
    accumulator = KernelMessageAccumulator(parent_msg_id="msg-1")
    messages = [
        {"parent_header": {"msg_id": "msg-1"}, "msg_type": "stream", "content": {"name": "stdout", "text": "hello\n"}},
        {"parent_header": {"msg_id": "msg-1"}, "msg_type": "stream", "content": {"name": "stderr", "text": "warn\n"}},
        {
            "parent_header": {"msg_id": "msg-1"},
            "msg_type": "execute_result",
            "content": {"data": {"text/plain": "42"}},
        },
        {"parent_header": {"msg_id": "msg-1"}, "msg_type": "execute_reply", "content": {"status": "ok"}},
        {"parent_header": {"msg_id": "msg-1"}, "msg_type": "status", "content": {"execution_state": "idle"}},
    ]

    for message in messages:
        accumulator.apply(message, allow_stdin=False)

    result = accumulator.to_cell_result(index=0, source="print('hello')")

    assert result.status == "success"
    assert result.stdout == "hello\n"
    assert result.stderr == "warn\n"
    assert result.outputs == [{"text/plain": "42"}]


def test_kernel_message_accumulator_collects_errors() -> None:
    accumulator = KernelMessageAccumulator(parent_msg_id="msg-1")

    accumulator.apply(
        {
            "parent_header": {"msg_id": "msg-1"},
            "msg_type": "error",
            "content": {
                "ename": "ValueError",
                "evalue": "bad value",
                "traceback": ["line 1", "line 2"],
            },
        },
        allow_stdin=False,
    )

    result = accumulator.to_cell_result(index=0, source="raise ValueError('bad value')")

    assert result.status == "error"
    assert result.error == "ValueError: bad value"
    assert result.traceback == ["line 1", "line 2"]


def test_kernel_message_accumulator_rejects_input_request_when_not_interactive() -> None:
    accumulator = KernelMessageAccumulator(parent_msg_id="msg-1")

    with pytest.raises(ExecutionError):
        accumulator.apply(
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "input_request",
                "content": {"prompt": "value: "},
            },
            allow_stdin=False,
        )


def _rest_client_returning(status_code: int) -> JupyterRestClient:
    return JupyterRestClient(
        base_url="https://proxy.example.com",
        access_token="access-token",
        proxy_token="proxy-token",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(status_code))
        ),
    )


@pytest.mark.asyncio
async def test_create_session_maps_404_to_runtime_error() -> None:
    client = _rest_client_returning(404)
    try:
        with pytest.raises(ColabRuntimeError):
            await client.create_session(path="/content/x.py", name="x.py")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_create_session_maps_403_to_auth_error() -> None:
    client = _rest_client_returning(403)
    try:
        with pytest.raises(AuthError):
            await client.create_session(path="/content/x.py", name="x.py")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_get_contents_maps_404_to_execution_error_naming_the_path() -> None:
    client = _rest_client_returning(404)
    try:
        with pytest.raises(ExecutionError, match="/content/missing.csv"):
            await client.get_contents("/content/missing.csv")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_rest_maps_transport_error_to_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network unreachable", request=request)

    client = JupyterRestClient(
        base_url="https://proxy.example.com",
        access_token="access-token",
        proxy_token="proxy-token",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        with pytest.raises(ConnectionError):
            await client.list_sessions()
    finally:
        await client.aclose()


def _ws_client() -> KernelWebSocketClient:
    return KernelWebSocketClient(
        base_url="https://proxy.example.com",
        access_token="access-token",
        proxy_token="proxy-token",
        kernel_id="kernel-123",
    )


class _ConnectCM:
    """Stand-in for websockets.connect: raises on enter or yields a fake socket."""

    def __init__(self, *, raises: Exception | None = None, websocket: object = None) -> None:
        self._raises = raises
        self._websocket = websocket

    async def __aenter__(self) -> object:
        if self._raises is not None:
            raise self._raises
        return self._websocket

    async def __aexit__(self, *args: object) -> bool:
        return False


def _handshake_failure(status_code: int) -> Exception:
    from websockets.datastructures import Headers
    from websockets.exceptions import InvalidStatus
    from websockets.http11 import Response

    return InvalidStatus(Response(status_code, "nope", Headers()))


@pytest.mark.asyncio
async def test_execute_maps_handshake_404_to_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import colab_cli.core.jupyter.ws as ws_mod

    monkeypatch.setattr(
        ws_mod.websockets, "connect", lambda *a, **k: _ConnectCM(raises=_handshake_failure(404))
    )

    with pytest.raises(ColabRuntimeError):
        await _ws_client().execute("print(1)")


@pytest.mark.asyncio
async def test_execute_maps_handshake_401_to_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import colab_cli.core.jupyter.ws as ws_mod

    monkeypatch.setattr(
        ws_mod.websockets, "connect", lambda *a, **k: _ConnectCM(raises=_handshake_failure(401))
    )

    with pytest.raises(AuthError):
        await _ws_client().execute("print(1)")


@pytest.mark.asyncio
async def test_execute_maps_mid_run_disconnect_to_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from websockets.exceptions import ConnectionClosedError

    import colab_cli.core.jupyter.ws as ws_mod

    class DroppingWebSocket:
        async def send(self, _: str) -> None:
            return None

        async def recv(self) -> str:
            raise ConnectionClosedError(None, None)

    monkeypatch.setattr(
        ws_mod.websockets,
        "connect",
        lambda *a, **k: _ConnectCM(websocket=DroppingWebSocket()),
    )

    with pytest.raises(ColabRuntimeError):
        await _ws_client().execute("print(1)")


def test_extract_code_cells_returns_only_code_sources(tmp_path: Path) -> None:
    notebook_path = tmp_path / "example.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "cells": [
                    {"cell_type": "markdown", "source": ["# Title"]},
                    {"cell_type": "code", "source": ["print('hello')\n"]},
                    {"cell_type": "code", "source": ["x = 1\n", "print(x)\n"]},
                ]
            }
        )
    )

    cells = extract_code_cells(notebook_path)

    assert cells == ["print('hello')\n", "x = 1\nprint(x)\n"]
