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
        self.timeouts: list[float | None] = []

    async def execute(self, code: str, *, cell_index: int = 0, allow_stdin: bool = False, on_stream=None, timeout_seconds: float | None = None) -> CellResult:
        self.calls.append(code)
        self.timeouts.append(timeout_seconds)
        if code.strip().startswith("raise"):
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
async def test_disconnect_tolerates_reclaimed_runtime(tmp_path: Path) -> None:
    """Colab reports the runtime as gone on unassign when it was already
    reclaimed. Local cleanup should still proceed so the user is not stuck
    with a stale active.json."""
    from colab_cli.errors import ColabRuntimeError

    connection_store = _connected_store(tmp_path)

    class StaleColabClient(FakeColabClient):
        async def unassign_runtime(self, **_: object) -> None:
            raise ColabRuntimeError("runtime already reclaimed")

    manager = RuntimeManager(
        config=make_config(),
        credentials=FakeCredentials(),
        connection_store=connection_store,
        colab_client_factory=StaleColabClient,
        spawn_keepalive=False,
    )

    status = await manager.disconnect()

    assert status.connected is False
    assert connection_store.load() is None


@pytest.mark.asyncio
async def test_disconnect_reraises_unexpected_errors(tmp_path: Path) -> None:
    from colab_cli.errors import ConnectionError

    connection_store = _connected_store(tmp_path)

    class BrokenColabClient(FakeColabClient):
        async def unassign_runtime(self, **_: object) -> None:
            raise ConnectionError("Colab API error HTTP 500")

    manager = RuntimeManager(
        config=make_config(),
        credentials=FakeCredentials(),
        connection_store=connection_store,
        colab_client_factory=BrokenColabClient,
        spawn_keepalive=False,
    )

    with pytest.raises(ConnectionError):
        await manager.disconnect()

    assert connection_store.load() is not None


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


@pytest.mark.asyncio
async def test_run_code_with_secrets_injects_setup_cell(tmp_path: Path) -> None:
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

    result = await manager.run_code("print('hello')", secrets={"MY_KEY": "my_val"})

    assert result.status == "success"
    assert len(kernel_client.calls) == 2
    assert "google.colab.userdata" in kernel_client.calls[0]
    assert kernel_client.calls[1] == "print('hello')"


@pytest.mark.asyncio
async def test_run_code_without_secrets_no_setup_cell(tmp_path: Path) -> None:
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

    result = await manager.run_code("print('hello')")

    assert result.status == "success"
    assert len(kernel_client.calls) == 1


@pytest.mark.asyncio
async def test_run_notebook_with_secrets_injects_setup_before_cells(tmp_path: Path) -> None:
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
    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text(
        '{"cells":[{"cell_type":"code","source":["print(1)"]},{"cell_type":"code","source":["print(2)"]}]}'
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

    result = await manager.run_notebook(notebook_path, secrets={"K": "V"})

    assert result.status == "success"
    # Setup cell + 2 user cells = 3 kernel calls
    assert len(kernel_client.calls) == 3
    assert "google.colab.userdata" in kernel_client.calls[0]
    # Setup cell should NOT be in the result cells
    assert len(result.cells) == 2


def test_create_runtime_manager_can_skip_missing_config_for_status(tmp_path: Path) -> None:
    manager = create_runtime_manager(home=tmp_path, spawn_keepalive=False, allow_missing_config=True)

    assert manager.status().connected is False


def _connected_store(tmp_path: Path) -> ConnectionStore:
    store = ConnectionStore(home=tmp_path)
    store.save(
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
    return store


@pytest.mark.asyncio
async def test_execute_cell_writes_output_back(tmp_path: Path) -> None:
    from colab_cli.core.notebook import NotebookDocument

    nb = tmp_path / "nb.ipynb"
    doc = NotebookDocument.create_empty(nb)
    doc.create_cell("code", "print(1)")
    doc.create_cell("code", "print(2)")
    doc.save()

    kernel_client = FakeKernelClient()
    manager = RuntimeManager(
        config=make_config(),
        credentials=FakeCredentials(),
        connection_store=_connected_store(tmp_path),
        colab_client_factory=FakeColabClient,
        jupyter_rest_factory=lambda **_: FakeJupyterRestClient(),
        kernel_client_factory=lambda **_: kernel_client,
        spawn_keepalive=False,
    )

    result = await manager.execute_cell(nb, 1)

    assert result.status == "success"
    assert kernel_client.calls == ["print(2)"]
    reloaded = NotebookDocument.load(nb)
    assert reloaded.state().cells[1].execution_count == 1
    assert reloaded.get_cell_output(1)


@pytest.mark.asyncio
async def test_execute_cell_rejects_non_code_cell(tmp_path: Path) -> None:
    from colab_cli.core.notebook import NotebookDocument
    from colab_cli.errors import ExecutionError

    nb = tmp_path / "nb.ipynb"
    doc = NotebookDocument.create_empty(nb)
    doc.create_cell("markdown", "# heading")
    doc.save()

    manager = RuntimeManager(
        config=make_config(),
        credentials=FakeCredentials(),
        connection_store=_connected_store(tmp_path),
        colab_client_factory=FakeColabClient,
        jupyter_rest_factory=lambda **_: FakeJupyterRestClient(),
        kernel_client_factory=lambda **_: FakeKernelClient(),
        spawn_keepalive=False,
    )

    with pytest.raises(ExecutionError):
        await manager.execute_cell(nb, 0)


@pytest.mark.asyncio
async def test_execute_cell_reuses_kernel_across_calls(tmp_path: Path) -> None:
    from colab_cli.core.notebook import NotebookDocument

    nb = tmp_path / "nb.ipynb"
    doc = NotebookDocument.create_empty(nb)
    doc.create_cell("code", "print(1)")
    doc.create_cell("code", "print(2)")
    doc.save()

    kernel_client = FakeKernelClient()
    manager = RuntimeManager(
        config=make_config(),
        credentials=FakeCredentials(),
        connection_store=_connected_store(tmp_path),
        colab_client_factory=FakeColabClient,
        jupyter_rest_factory=lambda **_: FakeJupyterRestClient(),
        kernel_client_factory=lambda **_: kernel_client,
        spawn_keepalive=False,
    )

    await manager.execute_cell(nb, 0)
    await manager.execute_cell(nb, 1)

    assert kernel_client.calls == ["print(1)", "print(2)"]


class ClosableJupyterClient(FakeJupyterRestClient):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_jupyter_client_closed_when_upload_fails(tmp_path: Path) -> None:
    from colab_cli.errors import ConnectionError

    class FailingClient(ClosableJupyterClient):
        async def upload_file(self, local_path: Path, remote_path: str):
            raise ConnectionError("proxy exploded")

    client = FailingClient()
    local = tmp_path / "f.txt"
    local.write_text("data")
    manager = RuntimeManager(
        config=make_config(),
        credentials=FakeCredentials(),
        connection_store=_connected_store(tmp_path),
        colab_client_factory=FakeColabClient,
        jupyter_rest_factory=lambda **_: client,
        spawn_keepalive=False,
    )

    with pytest.raises(ConnectionError):
        await manager.push_file(local, "/content/f.txt")

    assert client.closed


@pytest.mark.asyncio
async def test_jupyter_client_closed_when_create_session_fails(tmp_path: Path) -> None:
    from colab_cli.errors import ColabRuntimeError

    class FailingClient(ClosableJupyterClient):
        async def create_session(self, **kwargs: object):
            raise ColabRuntimeError("runtime reclaimed")

    client = FailingClient()
    manager = RuntimeManager(
        config=make_config(),
        credentials=FakeCredentials(),
        connection_store=_connected_store(tmp_path),
        colab_client_factory=FakeColabClient,
        jupyter_rest_factory=lambda **_: client,
        kernel_client_factory=lambda **_: FakeKernelClient(),
        spawn_keepalive=False,
    )

    with pytest.raises(ColabRuntimeError):
        await manager.run_code("print(1)")

    assert client.closed


@pytest.mark.asyncio
async def test_run_code_forwards_timeout_to_kernel(tmp_path: Path) -> None:
    kernel_client = FakeKernelClient()
    manager = RuntimeManager(
        config=make_config(),
        credentials=FakeCredentials(),
        connection_store=_connected_store(tmp_path),
        colab_client_factory=FakeColabClient,
        jupyter_rest_factory=lambda **_: FakeJupyterRestClient(),
        kernel_client_factory=lambda **_: kernel_client,
        spawn_keepalive=False,
    )

    await manager.run_code("print(1)", timeout=12.5)

    assert kernel_client.timeouts[-1] == 12.5


@pytest.mark.asyncio
async def test_keepalive_once_preserves_session_written_during_keepalive(tmp_path: Path) -> None:
    """Keepalive must not clobber session info another process saved mid-cycle."""
    store = _connected_store(tmp_path)

    class InterleavingColabClient(FakeColabClient):
        async def keep_alive(self, **kwargs: object) -> None:
            # Simulate the main process persisting session info between
            # keepalive's load and its save.
            conn = store.load()
            assert conn is not None
            conn.session_id = "session-xyz"
            conn.kernel_id = "kernel-xyz"
            store.save(conn)

    manager = RuntimeManager(
        config=make_config(),
        credentials=FakeCredentials(),
        connection_store=store,
        colab_client_factory=InterleavingColabClient,
        spawn_keepalive=False,
    )

    await manager.keepalive_once()

    stored = store.load()
    assert stored is not None
    assert stored.session_id == "session-xyz"
    assert stored.kernel_id == "kernel-xyz"
    assert stored.last_keepalive_at is not None


@pytest.mark.asyncio
async def test_ensure_session_preserves_keepalive_written_during_session_create(tmp_path: Path) -> None:
    """Session creation must not clobber keepalive state saved mid-cycle."""
    store = _connected_store(tmp_path)
    ts = datetime.now(UTC)

    class InterleavingJupyterClient(FakeJupyterRestClient):
        async def create_session(self, **kwargs: object) -> JupyterSession:
            # Simulate the keepalive subprocess persisting a heartbeat between
            # the main process's load and its save.
            conn = store.load()
            assert conn is not None
            conn.last_keepalive_at = ts
            store.save(conn)
            return await super().create_session(**kwargs)

    manager = RuntimeManager(
        config=make_config(),
        credentials=FakeCredentials(),
        connection_store=store,
        colab_client_factory=FakeColabClient,
        jupyter_rest_factory=lambda **_: InterleavingJupyterClient(),
        kernel_client_factory=lambda **_: FakeKernelClient(),
        spawn_keepalive=False,
    )

    await manager.run_code("print('hi')")

    stored = store.load()
    assert stored is not None
    assert stored.session_id == "session-123"
    assert stored.last_keepalive_at == ts
