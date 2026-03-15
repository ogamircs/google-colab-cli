from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from colab_cli.core.jupyter.rest import JupyterRestClient, decode_contents_payload, encode_contents_payload
from colab_cli.core.jupyter.ws import KernelMessageAccumulator
from colab_cli.errors import ExecutionError
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
