"""Kernel WebSocket helpers for executing code on a Colab runtime."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from typing import Any

import websockets

from colab_cli.core.colab.headers import build_colab_headers
from colab_cli.errors import ExecutionError
from colab_cli.models import CellResult


class KernelMessageAccumulator:
    def __init__(self, *, parent_msg_id: str) -> None:
        self.parent_msg_id = parent_msg_id
        self.stdout = ""
        self.stderr = ""
        self.outputs: list[dict[str, Any]] = []
        self.error: str | None = None
        self.traceback: list[str] | None = None
        self.reply_status: str | None = None
        self.idle = False

    @property
    def is_complete(self) -> bool:
        return self.reply_status is not None and self.idle

    def apply(self, message: dict[str, Any], *, allow_stdin: bool) -> None:
        if message.get("parent_header", {}).get("msg_id") != self.parent_msg_id:
            return

        msg_type = message.get("msg_type")
        content = message.get("content", {})

        if msg_type == "stream":
            target = content.get("name", "stdout")
            if target == "stderr":
                self.stderr += content.get("text", "")
            else:
                self.stdout += content.get("text", "")
        elif msg_type in {"execute_result", "display_data"}:
            data = content.get("data", {})
            if isinstance(data, dict) and data:
                self.outputs.append(data)
        elif msg_type == "error":
            ename = content.get("ename", "Error")
            evalue = content.get("evalue", "")
            self.error = f"{ename}: {evalue}".strip(": ")
            self.traceback = list(content.get("traceback", []))
            self.reply_status = "error"
        elif msg_type == "execute_reply":
            self.reply_status = content.get("status", "ok")
        elif msg_type == "status" and content.get("execution_state") == "idle":
            self.idle = True
        elif msg_type == "input_request" and not allow_stdin:
            raise ExecutionError("Remote code requested stdin input in non-interactive mode.")

    def to_cell_result(self, *, index: int, source: str) -> CellResult:
        status = "error" if self.error or self.reply_status == "error" else "success"
        return CellResult(
            index=index,
            source=source,
            status=status,
            stdout=self.stdout,
            stderr=self.stderr,
            outputs=self.outputs,
            error=self.error,
            traceback=self.traceback,
        )


class KernelWebSocketClient:
    def __init__(
        self,
        *,
        base_url: str,
        access_token: str,
        proxy_token: str,
        kernel_id: str,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.proxy_token = proxy_token
        self.kernel_id = kernel_id

    async def execute(
        self,
        code: str,
        *,
        cell_index: int = 0,
        allow_stdin: bool = False,
        on_stream: Callable[[str, str], Any] | None = None,
        timeout_seconds: float = 300.0,
    ) -> CellResult:
        ws_url = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        session_id = uuid.uuid4().hex
        ws_url = f"{ws_url}/api/kernels/{self.kernel_id}/channels?session_id={session_id}"
        msg_id = uuid.uuid4().hex
        accumulator = KernelMessageAccumulator(parent_msg_id=msg_id)
        async with websockets.connect(
            ws_url,
            additional_headers=build_colab_headers(
                self.access_token,
                proxy_token=self.proxy_token,
            ),
            ping_interval=None,  # Disable auto-ping; Colab proxy may not forward pings
            ping_timeout=None,
            open_timeout=30,
            close_timeout=10,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "channel": "shell",
                        "header": {
                            "msg_id": msg_id,
                            "username": "colab-cli",
                            "session": session_id,
                            "msg_type": "execute_request",
                            "version": "5.3",
                        },
                        "parent_header": {},
                        "metadata": {},
                        "content": {
                            "code": code,
                            "silent": False,
                            "store_history": True,
                            "user_expressions": {},
                            "allow_stdin": allow_stdin,
                            "stop_on_error": True,
                        },
                    }
                )
            )
            await self._drain_messages(
                websocket,
                accumulator=accumulator,
                on_stream=on_stream,
                allow_stdin=allow_stdin,
                timeout_seconds=timeout_seconds,
            )
        return accumulator.to_cell_result(index=cell_index, source=code)

    async def _drain_messages(
        self,
        websocket: websockets.ClientConnection,
        *,
        accumulator: KernelMessageAccumulator,
        on_stream: Callable[[str, str], Any] | None,
        allow_stdin: bool,
        timeout_seconds: float,
    ) -> None:
        while not accumulator.is_complete:
            raw_message = await asyncio.wait_for(websocket.recv(), timeout=timeout_seconds)
            message = json.loads(raw_message)
            accumulator.apply(message, allow_stdin=allow_stdin)
            if on_stream and message.get("msg_type") == "stream":
                await _maybe_await(on_stream(message["content"].get("name", "stdout"), message["content"].get("text", "")))


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value

