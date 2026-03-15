"""High-level runtime orchestration for connect/run/file commands."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from colab_cli.config import load_app_config
from colab_cli.core.auth.credentials import CredentialManager
from colab_cli.core.colab.client import ColabClient
from colab_cli.core.connection import ConnectionStore
from colab_cli.core.jupyter.rest import JupyterRestClient
from colab_cli.core.jupyter.ws import KernelWebSocketClient
from colab_cli.errors import ConfigError, ConnectionError
from colab_cli.formats.notebook import extract_code_cells
from colab_cli.models import (
    ActiveConnection,
    AppConfig,
    CellResult,
    RunResult,
    StatusResult,
)
from colab_cli.utils import generate_notebook_hash, should_refresh_soon, ttl_to_expiry, utc_now


class RuntimeManager:
    def __init__(
        self,
        *,
        config: AppConfig,
        credentials: CredentialManager,
        connection_store: ConnectionStore | None = None,
        colab_client_factory: Callable[[], Any] = ColabClient,
        jupyter_rest_factory: Callable[..., Any] = JupyterRestClient,
        kernel_client_factory: Callable[..., Any] = KernelWebSocketClient,
        spawn_keepalive: bool = True,
    ) -> None:
        self.config = config
        self.credentials = credentials
        self.connection_store = connection_store or ConnectionStore()
        self._colab_client_factory = colab_client_factory
        self._jupyter_rest_factory = jupyter_rest_factory
        self._kernel_client_factory = kernel_client_factory
        self._spawn_keepalive = spawn_keepalive

    async def connect(
        self,
        *,
        accelerator: str | None = None,
        authuser: int | None = None,
    ) -> StatusResult:
        existing = self.connection_store.load()
        if existing is not None:
            return self.status()

        token = self.credentials.get_valid_token()
        accelerator_name = accelerator or self.config.default_accelerator
        authuser_value = self.config.default_authuser if authuser is None else authuser
        notebook_hash = generate_notebook_hash()

        client = self._colab_client_factory()
        try:
            assigned = await client.assign_runtime(
                access_token=token.access_token,
                notebook_hash=notebook_hash,
                variant="GPU" if accelerator_name else None,
                accelerator=accelerator_name.upper() if accelerator_name else None,
                authuser=authuser_value,
            )
            # Prefer proxy info from assign response; fall back to separate fetch
            rpi = assigned.runtime_proxy_info
            if rpi and rpi.url and rpi.token:
                proxy_url = rpi.url
                proxy_token = rpi.token
                proxy_expires_at = ttl_to_expiry(rpi.token_expires_in_seconds or 3600)
            else:
                proxy = await client.fetch_runtime_proxy_token(
                    access_token=token.access_token,
                    endpoint_id=assigned.endpoint,
                )
                proxy_url = proxy.url
                proxy_token = proxy.token
                proxy_expires_at = ttl_to_expiry(_parse_ttl_seconds(proxy.token_ttl))
        finally:
            await _maybe_aclose(client)

        connection = ActiveConnection(
            notebook_hash=notebook_hash,
            endpoint_id=assigned.endpoint,
            proxy_url=proxy_url,
            proxy_token=proxy_token,
            proxy_expires_at=proxy_expires_at,
            accelerator=assigned.accelerator or accelerator_name.upper() if accelerator_name else assigned.accelerator,
            authuser=authuser_value,
        )
        if self._spawn_keepalive:
            connection.keepalive_pid = self._spawn_keepalive_process()
        self.connection_store.save(connection)
        return self.status()

    def status(self) -> StatusResult:
        connection = self.connection_store.load()
        if connection is None:
            return StatusResult(connected=False)
        return StatusResult(
            connected=True,
            endpoint=connection.endpoint_id,
            accelerator=connection.accelerator,
            proxy_expires_at=connection.proxy_expires_at,
            last_keepalive_at=connection.last_keepalive_at,
            notebook_hash=connection.notebook_hash,
        )

    async def disconnect(self) -> StatusResult:
        connection = self.connection_store.load()
        if connection is None:
            return StatusResult(connected=False)
        token = self.credentials.get_valid_token()
        client = self._colab_client_factory()
        try:
            await client.unassign_runtime(
                access_token=token.access_token,
                endpoint_id=connection.endpoint_id,
                authuser=connection.authuser,
            )
        finally:
            await _maybe_aclose(client)
        self._stop_keepalive_process(connection.keepalive_pid)
        self.connection_store.delete()
        return StatusResult(connected=False)

    async def run_code(
        self,
        code: str,
        *,
        source_name: str = "inline.py",
        allow_stdin: bool = False,
        on_stream: Callable[[str, str], Any] | None = None,
    ) -> RunResult:
        started = time.monotonic()
        connection = await self._ensure_session(source_name=source_name)
        token = self.credentials.get_valid_token()
        kernel_client = self._kernel_client_factory(
            base_url=connection.proxy_url,
            access_token=token.access_token,
            proxy_token=connection.proxy_token,
            kernel_id=connection.kernel_id,
        )
        cell = await kernel_client.execute(
            code,
            cell_index=0,
            allow_stdin=allow_stdin,
            on_stream=on_stream,
        )
        return _cell_to_run_result(cell, duration_seconds=time.monotonic() - started)

    async def run_script(
        self,
        path: Path,
        *,
        allow_stdin: bool = False,
        on_stream: Callable[[str, str], Any] | None = None,
    ) -> RunResult:
        return await self.run_code(
            path.read_text(),
            source_name=path.name,
            allow_stdin=allow_stdin,
            on_stream=on_stream,
        )

    async def run_notebook(
        self,
        path: Path,
        *,
        allow_stdin: bool = False,
        on_stream: Callable[[str, str], Any] | None = None,
        on_cell_start: Callable[[int, int], Any] | None = None,
    ) -> RunResult:
        started = time.monotonic()
        connection = await self._ensure_session(source_name=path.name)
        token = self.credentials.get_valid_token()
        kernel_client = self._kernel_client_factory(
            base_url=connection.proxy_url,
            access_token=token.access_token,
            proxy_token=connection.proxy_token,
            kernel_id=connection.kernel_id,
        )
        cells = extract_code_cells(path)
        results: list[CellResult] = []
        for index, code in enumerate(cells):
            if on_cell_start:
                maybe = on_cell_start(index + 1, len(cells))
                if asyncio.iscoroutine(maybe):
                    await maybe
            cell_result = await kernel_client.execute(
                code,
                cell_index=index,
                allow_stdin=allow_stdin,
                on_stream=on_stream,
            )
            results.append(cell_result)
            if cell_result.status == "error":
                break

        stdout = "".join(cell.stdout for cell in results)
        stderr = "".join(cell.stderr for cell in results)
        errored = next((cell for cell in results if cell.status == "error"), None)
        return RunResult(
            status="error" if errored else "success",
            exit_code=1 if errored else 0,
            stdout=stdout,
            stderr=stderr,
            error=errored.error if errored else None,
            traceback=errored.traceback if errored else None,
            duration_seconds=time.monotonic() - started,
            cells=results,
        )

    async def push_file(self, local_path: Path, remote_path: str) -> None:
        connection = await self._ensure_connection()
        token = self.credentials.get_valid_token()
        client = self._jupyter_rest_factory(
            base_url=connection.proxy_url,
            access_token=token.access_token,
            proxy_token=connection.proxy_token,
        )
        await client.upload_file(local_path, remote_path)
        await _maybe_aclose(client)

    async def pull_file(self, remote_path: str, local_path: Path) -> Path:
        connection = await self._ensure_connection()
        token = self.credentials.get_valid_token()
        client = self._jupyter_rest_factory(
            base_url=connection.proxy_url,
            access_token=token.access_token,
            proxy_token=connection.proxy_token,
        )
        result = await client.download_file(remote_path, local_path)
        await _maybe_aclose(client)
        return result

    async def list_files(self, remote_path: str = "") -> list[Any]:
        connection = await self._ensure_connection()
        token = self.credentials.get_valid_token()
        client = self._jupyter_rest_factory(
            base_url=connection.proxy_url,
            access_token=token.access_token,
            proxy_token=connection.proxy_token,
        )
        items = await client.list_directory(remote_path)
        await _maybe_aclose(client)
        return items

    async def keepalive_once(self) -> StatusResult:
        connection = self.connection_store.load()
        if connection is None:
            return StatusResult(connected=False)
        token = self.credentials.get_valid_token()
        client = self._colab_client_factory()
        try:
            await client.keep_alive(
                access_token=token.access_token,
                endpoint_id=connection.endpoint_id,
                authuser=connection.authuser,
            )
            if should_refresh_soon(connection.proxy_expires_at, threshold=timedelta(minutes=5)):
                proxy = await client.fetch_runtime_proxy_token(
                    access_token=token.access_token,
                    endpoint_id=connection.endpoint_id,
                )
                connection.proxy_url = proxy.url
                connection.proxy_token = proxy.token
                connection.proxy_expires_at = ttl_to_expiry(_parse_ttl_seconds(proxy.token_ttl))
            connection.last_keepalive_at = utc_now()
            self.connection_store.save(connection)
        finally:
            await _maybe_aclose(client)
        return self.status()

    async def _ensure_connection(self) -> ActiveConnection:
        connection = self.connection_store.load()
        if connection is None:
            raise ConnectionError("No active Colab runtime. Run `colab connect` first.")
        if should_refresh_soon(connection.proxy_expires_at, threshold=timedelta(minutes=5)):
            token = self.credentials.get_valid_token()
            client = self._colab_client_factory()
            try:
                proxy = await client.fetch_runtime_proxy_token(
                    access_token=token.access_token,
                    endpoint_id=connection.endpoint_id,
                )
            finally:
                await _maybe_aclose(client)
            connection.proxy_url = proxy.url
            connection.proxy_token = proxy.token
            connection.proxy_expires_at = ttl_to_expiry(_parse_ttl_seconds(proxy.token_ttl))
            self.connection_store.save(connection)
        return connection

    async def _ensure_session(self, *, source_name: str) -> ActiveConnection:
        connection = await self._ensure_connection()
        if connection.session_id and connection.kernel_id:
            return connection

        token = self.credentials.get_valid_token()
        client = self._jupyter_rest_factory(
            base_url=connection.proxy_url,
            access_token=token.access_token,
            proxy_token=connection.proxy_token,
        )
        session = await client.create_session(
            path=f"/content/{source_name}",
            name=source_name,
            session_type="notebook",
        )
        await _maybe_aclose(client)
        connection.session_id = session.id
        connection.kernel_id = session.kernel.id
        self.connection_store.save(connection)
        return connection

    def _spawn_keepalive_process(self) -> int | None:
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "colab_cli.cli",
                    "_internal_keepalive",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            return None
        return process.pid

    def _stop_keepalive_process(self, pid: int | None) -> None:
        if pid is None:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return


def create_runtime_manager(
    *,
    home: Path | None = None,
    spawn_keepalive: bool = True,
    allow_missing_config: bool = False,
) -> RuntimeManager:
    try:
        config = load_app_config(home=home)
    except ConfigError:
        if not allow_missing_config:
            raise
        config = AppConfig(
            oauth={
                "client_id": "placeholder.apps.googleusercontent.com",
                "client_secret": "placeholder",
            }
        )
    credentials = CredentialManager(config=config, home=home)
    connection_store = ConnectionStore(home=home)
    return RuntimeManager(
        config=config,
        credentials=credentials,
        connection_store=connection_store,
        spawn_keepalive=spawn_keepalive,
    )


def _parse_ttl_seconds(token_ttl: str | None) -> int:
    if not token_ttl:
        return 3600
    return int(str(token_ttl).rstrip("s"))


def _cell_to_run_result(cell: CellResult, *, duration_seconds: float) -> RunResult:
    return RunResult(
        status=cell.status,
        exit_code=0 if cell.status == "success" else 1,
        stdout=cell.stdout,
        stderr=cell.stderr,
        error=cell.error,
        traceback=cell.traceback,
        duration_seconds=duration_seconds,
        cells=[cell],
    )


async def _maybe_aclose(client: Any) -> None:
    aclose = getattr(client, "aclose", None)
    if callable(aclose):
        result = aclose()
        if asyncio.iscoroutine(result):
            await result
