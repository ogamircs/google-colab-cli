from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from colab_cli.core.connection import ConnectionStore
from colab_cli.core.runtime import RuntimeManager, create_runtime_manager
from colab_cli.models import (
    ActiveConnection,
    AppConfig,
    AssignedRuntime,
    CellResult,
    JupyterContent,
    JupyterSession,
    JupyterSessionKernel,
    OAuthConfig,
    RuntimeProxyTokenResponse,
    TokenData,
)


class FakeCredentials:
    def __init__(self) -> None:
        self.token = TokenData(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scope="openid email profile",
            token_type="Bearer",
        )

    def get_valid_token(self) -> TokenData:
        return self.token


class FakeColabClient:
    def __init__(self) -> None:
        self.assign_calls = 0
        self.unassigned: list[str] = []

    async def assign_runtime(self, **_: object) -> AssignedRuntime:
        self.assign_calls += 1
        return AssignedRuntime(
            endpoint="endpoint-123",
            accelerator="T4",
            runtimeProxyInfo={
                "url": "https://proxy.example.com",
                "token": "proxy-token",
                "tokenExpiresInSeconds": 3600,
            },
        )

    async def fetch_runtime_proxy_token(self, **_: object) -> RuntimeProxyTokenResponse:
        return RuntimeProxyTokenResponse(
            token="proxy-token",
            url="https://proxy.example.com",
            tokenTtl="3600s",
        )

    async def keep_alive(self, **_: object) -> None:
        return None

    async def unassign_runtime(self, *, endpoint_id: str, **_: object) -> None:
        self.unassigned.append(endpoint_id)

    async def aclose(self) -> None:
        return None


class FakeJupyterRestClient:
    def __init__(self) -> None:
        self.uploads: list[tuple[Path, str]] = []
        self.downloads: list[tuple[str, Path]] = []

    async def create_session(self, *, path: str, name: str, session_type: str = "notebook", kernel_name: str = "python3") -> JupyterSession:
        return JupyterSession(
            id="session-123",
            path=path,
            name=name,
            type=session_type,
            kernel=JupyterSessionKernel(id="kernel-123", name=kernel_name),
        )

    async def upload_file(self, local_path: Path, remote_path: str) -> JupyterContent:
        self.uploads.append((local_path, remote_path))
        return JupyterContent(name=local_path.name, path=remote_path, type="file")

    async def download_file(self, remote_path: str, local_path: Path) -> Path:
        self.downloads.append((remote_path, local_path))
        local_path.write_text("downloaded")
        return local_path

    async def list_directory(self, path: str = "") -> list[JupyterContent]:
        return [JupyterContent(name="example.txt", path=f"{path}/example.txt", type="file")]


class FakeKernelClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, code: str, *, cell_index: int = 0, allow_stdin: bool = False, on_stream=None, timeout_seconds: float = 300.0) -> CellResult:
        self.calls.append(code)
        if "raise" in code:
            return CellResult(
                index=cell_index,
                source=code,
                status="error",
                error="ValueError: boom",
                traceback=["Traceback", "ValueError: boom"],
            )
        return CellResult(
            index=cell_index,
            source=code,
            status="success",
            stdout=f"ran:{cell_index}\n",
        )


def make_config() -> AppConfig:
    return AppConfig(
        oauth=OAuthConfig(
            client_id="client.apps.googleusercontent.com",
            client_secret="secret",
        ),
        default_accelerator="t4",
    )


@pytest.mark.asyncio
async def test_connect_saves_active_connection(tmp_path: Path) -> None:
    colab_client = FakeColabClient()
    manager = RuntimeManager(
        config=make_config(),
        credentials=FakeCredentials(),
        connection_store=ConnectionStore(home=tmp_path),
        colab_client_factory=lambda: colab_client,
        spawn_keepalive=False,
    )

    status = await manager.connect(accelerator="t4")

    stored = manager.connection_store.load()
    assert status.connected is True
    assert stored is not None
    assert stored.endpoint_id == "endpoint-123"
    assert colab_client.assign_calls == 1


@pytest.mark.asyncio
async def test_connect_reuses_existing_connection(tmp_path: Path) -> None:
    connection_store = ConnectionStore(home=tmp_path)
    existing = ActiveConnection(
        notebook_hash="existing-hash",
        endpoint_id="existing-endpoint",
        proxy_url="https://proxy.example.com",
        proxy_token="existing-token",
        proxy_expires_at=datetime.now(UTC) + timedelta(hours=1),
        accelerator="T4",
        authuser=0,
        keepalive_pid=4321,
    )
    connection_store.save(existing)
    colab_client = FakeColabClient()
    manager = RuntimeManager(
        config=make_config(),
        credentials=FakeCredentials(),
        connection_store=connection_store,
        colab_client_factory=lambda: colab_client,
        spawn_keepalive=False,
    )

    status = await manager.connect(accelerator="t4")

    stored = connection_store.load()
    assert status.connected is True
    assert status.endpoint == "existing-endpoint"
    assert stored is not None
    assert stored.endpoint_id == "existing-endpoint"
    assert stored.keepalive_pid == 4321
    assert colab_client.assign_calls == 0


@pytest.mark.asyncio
async def test_run_code_creates_session_and_returns_run_result(tmp_path: Path) -> None:
    connection_store = ConnectionStore(home=tmp_path)
    connection_store.save(
        ActiveConnection(
            notebook_hash="hash",
            endpoint_id="endpoint-123",
            proxy_url="https://proxy.example.com",
            proxy_token="proxy-token",
            proxy_expires_at=datetime.now(UTC) + timedelta(hours=1),
            accelerator="T4",
            authuser=0,
        )
    )
    kernel_client = FakeKernelClient()
    manager = RuntimeManager(
        config=make_config(),
        credentials=FakeCredentials(),
        connection_store=connection_store,
        colab_client_factory=FakeColabClient,
        jupyter_rest_factory=lambda **_: FakeJupyterRestClient(),
        kernel_client_factory=lambda **_: kernel_client,
        spawn_keepalive=False,
    )

    result = await manager.run_code("print('hello')", source_name="script.py")

    refreshed = connection_store.load()
    assert result.status == "success"
    assert result.stdout == "ran:0\n"
    assert refreshed is not None
    assert refreshed.session_id == "session-123"
    assert refreshed.kernel_id == "kernel-123"


@pytest.mark.asyncio
async def test_run_notebook_accumulates_cells_and_stops_on_error(tmp_path: Path) -> None:
    connection_store = ConnectionStore(home=tmp_path)
    connection_store.save(
        ActiveConnection(
            notebook_hash="hash",
            endpoint_id="endpoint-123",
            proxy_url="https://proxy.example.com",
            proxy_token="proxy-token",
            proxy_expires_at=datetime.now(UTC) + timedelta(hours=1),
            accelerator="T4",
            authuser=0,
        )
    )
    notebook_path = tmp_path / "example.ipynb"
    notebook_path.write_text(
        '{"cells":[{"cell_type":"code","source":["print(1)"]},{"cell_type":"code","source":["raise ValueError()"]}]}'
    )
    manager = RuntimeManager(
        config=make_config(),
        credentials=FakeCredentials(),
        connection_store=connection_store,
        colab_client_factory=FakeColabClient,
        jupyter_rest_factory=lambda **_: FakeJupyterRestClient(),
        kernel_client_factory=lambda **_: FakeKernelClient(),
        spawn_keepalive=False,
    )

    result = await manager.run_notebook(notebook_path)

    assert result.status == "error"
    assert [cell.status for cell in result.cells] == ["success", "error"]


@pytest.mark.asyncio
async def test_disconnect_clears_connection(tmp_path: Path) -> None:
    connection_store = ConnectionStore(home=tmp_path)
    connection_store.save(
        ActiveConnection(
            notebook_hash="hash",
            endpoint_id="endpoint-123",
            proxy_url="https://proxy.example.com",
            proxy_token="proxy-token",
            proxy_expires_at=datetime.now(UTC) + timedelta(hours=1),
            accelerator="T4",
            authuser=0,
        )
    )
    colab_client = FakeColabClient()
    manager = RuntimeManager(
        config=make_config(),
        credentials=FakeCredentials(),
        connection_store=connection_store,
        colab_client_factory=lambda: colab_client,
        spawn_keepalive=False,
    )

    status = await manager.disconnect()

    assert status.connected is False
    assert connection_store.load() is None
    assert colab_client.unassigned == ["endpoint-123"]


@pytest.mark.asyncio
async def test_push_pull_and_ls_use_jupyter_client(tmp_path: Path) -> None:
    connection_store = ConnectionStore(home=tmp_path)
    connection_store.save(
        ActiveConnection(
            notebook_hash="hash",
            endpoint_id="endpoint-123",
            proxy_url="https://proxy.example.com",
            proxy_token="proxy-token",
            proxy_expires_at=datetime.now(UTC) + timedelta(hours=1),
            accelerator="T4",
            authuser=0,
        )
    )
    local_file = tmp_path / "upload.txt"
    local_file.write_text("hello")
    download_path = tmp_path / "download.txt"
    jupyter = FakeJupyterRestClient()
    manager = RuntimeManager(
        config=make_config(),
        credentials=FakeCredentials(),
        connection_store=connection_store,
        colab_client_factory=FakeColabClient,
        jupyter_rest_factory=lambda **_: jupyter,
        kernel_client_factory=lambda **_: FakeKernelClient(),
        spawn_keepalive=False,
    )

    await manager.push_file(local_file, "/content/upload.txt")
    await manager.pull_file("/content/upload.txt", download_path)
    items = await manager.list_files("/content")

    assert jupyter.uploads == [(local_file, "/content/upload.txt")]
    assert jupyter.downloads == [("/content/upload.txt", download_path)]
    assert items[0].name == "example.txt"


def test_create_runtime_manager_can_skip_missing_config_for_status(tmp_path: Path) -> None:
    manager = create_runtime_manager(home=tmp_path, spawn_keepalive=False, allow_missing_config=True)

    assert manager.status().connected is False
