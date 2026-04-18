from __future__ import annotations

import asyncio
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cloudpickle
import pytest

from colab_cli.api import ColabSession, colab, remote
from colab_cli.api._harness import DONE_MARKER
from colab_cli.api._sync import SyncRunner, reset_runner
from colab_cli.core.connection import ConnectionStore
from colab_cli.core.runtime import RuntimeManager
from colab_cli.errors import ConnectionError, RemoteExecutionError
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


# ------------------------------------------------------------------- fakes


class FakeCreds:
    def __init__(self) -> None:
        self.token = TokenData(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scope="openid email",
            token_type="Bearer",
        )

    def get_valid_token(self) -> TokenData:
        return self.token


class FakeColab:
    def __init__(self) -> None:
        self.assign_calls = 0
        self.unassigned: list[str] = []

    async def assign_runtime(self, **_: object) -> AssignedRuntime:
        self.assign_calls += 1
        return AssignedRuntime(
            endpoint="ep",
            accelerator="T4",
            runtimeProxyInfo={
                "url": "https://proxy.example.com",
                "token": "proxy-token",
                "tokenExpiresInSeconds": 3600,
            },
        )

    async def fetch_runtime_proxy_token(self, **_: object) -> RuntimeProxyTokenResponse:
        return RuntimeProxyTokenResponse(
            token="proxy-token", url="https://proxy.example.com", tokenTtl="3600s"
        )

    async def keep_alive(self, **_: object) -> None:
        return None

    async def unassign_runtime(self, *, endpoint_id: str, **_: object) -> None:
        self.unassigned.append(endpoint_id)

    async def aclose(self) -> None:
        return None


class FakeFS:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}


class FakeRest:
    def __init__(self, fs: FakeFS) -> None:
        self.fs = fs
        self.uploads: list[tuple[Path, str]] = []
        self.downloads: list[tuple[str, Path]] = []

    async def create_session(
        self,
        *,
        path: str,
        name: str,
        session_type: str = "notebook",
        kernel_name: str = "python3",
    ) -> JupyterSession:
        return JupyterSession(
            id="session-1",
            path=path,
            name=name,
            type=session_type,
            kernel=JupyterSessionKernel(id="kernel-1", name=kernel_name),
        )

    async def upload_file(self, local_path: Path, remote_path: str) -> JupyterContent:
        self.uploads.append((local_path, remote_path))
        self.fs.files[remote_path] = Path(local_path).read_bytes()
        return JupyterContent(
            name=Path(remote_path).name, path=remote_path, type="file"
        )

    async def download_file(self, remote_path: str, local_path: Path) -> Path:
        self.downloads.append((remote_path, local_path))
        Path(local_path).write_bytes(self.fs.files[remote_path])
        return Path(local_path)

    async def list_directory(self, path: str = "") -> list[JupyterContent]:
        return []

    async def aclose(self) -> None:
        return None


class FakeKernel:
    """Simulates harness execution against the in-memory FakeFS."""

    def __init__(
        self,
        fs: FakeFS,
        *,
        hardcoded_payload: object | None = None,
        harness_status: str = "success",
    ) -> None:
        self.fs = fs
        self.hardcoded_payload = hardcoded_payload
        self.harness_status = harness_status
        self.calls: list[str] = []

    async def execute(
        self,
        code: str,
        *,
        cell_index: int = 0,
        allow_stdin: bool = False,
        on_stream=None,
        timeout_seconds: float = 300.0,
    ) -> CellResult:
        self.calls.append(code)
        if "_colab_cli_run" in code:
            match = re.search(r"_SLUG_DIR = '([^']+)'", code)
            assert match, "harness code missing _SLUG_DIR"
            slug = match.group(1)
            if self.harness_status == "error":
                return CellResult(
                    index=cell_index,
                    source=code,
                    status="error",
                    error="harness boom",
                    traceback=["tb"],
                )
            if self.hardcoded_payload is not None:
                payload = self.hardcoded_payload
            else:
                fn = cloudpickle.loads(self.fs.files[f"{slug}/fn.pkl"])
                args, kwargs = cloudpickle.loads(self.fs.files[f"{slug}/args.pkl"])
                try:
                    value = fn(*args, **kwargs)
                    payload = ("ok", value)
                except BaseException as exc:  # noqa: BLE001
                    payload = ("err", exc, ["tb-from-fake"])
            self.fs.files[f"{slug}/result.pkl"] = cloudpickle.dumps(payload)
            return CellResult(
                index=cell_index,
                source=code,
                status="success",
                stdout=f"{DONE_MARKER}:{slug}/result.pkl\n",
            )
        # Non-harness: no-op success
        return CellResult(
            index=cell_index, source=code, status="success", stdout=""
        )


def make_config() -> AppConfig:
    return AppConfig(oauth=OAuthConfig(client_id="c.apps", client_secret="s"))


def seed_connection(accelerator: str = "T4") -> ActiveConnection:
    return ActiveConnection(
        notebook_hash="hash",
        endpoint_id="ep",
        proxy_url="https://proxy.example.com",
        proxy_token="proxy-token",
        proxy_expires_at=datetime.now(UTC) + timedelta(hours=1),
        accelerator=accelerator,
        authuser=0,
    )


def build_manager(
    tmp_path: Path,
    *,
    seed: ActiveConnection | None = None,
    fs: FakeFS | None = None,
    kernel: FakeKernel | None = None,
    colab_client: FakeColab | None = None,
) -> tuple[RuntimeManager, FakeFS, FakeRest, FakeKernel, FakeColab, ConnectionStore]:
    fs = fs or FakeFS()
    store = ConnectionStore(home=tmp_path)
    if seed is not None:
        store.save(seed)
    rest = FakeRest(fs)
    kernel = kernel or FakeKernel(fs)
    colab_client = colab_client or FakeColab()
    mgr = RuntimeManager(
        config=make_config(),
        credentials=FakeCreds(),
        connection_store=store,
        colab_client_factory=lambda: colab_client,
        jupyter_rest_factory=lambda **_: rest,
        kernel_client_factory=lambda **_: kernel,
        spawn_keepalive=False,
    )
    return mgr, fs, rest, kernel, colab_client, store


# --------------------------------------------------------------- fixtures


@pytest.fixture
def runner():
    r = SyncRunner()
    try:
        yield r
    finally:
        r.close()


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_runner()
    yield
    reset_runner()


# ------------------------------------------------------------ lifecycle tests


def test_colab_auto_connects_when_no_active_connection(tmp_path: Path, runner) -> None:
    mgr, _, _, _, colab_client, store = build_manager(tmp_path)

    session = colab(manager=mgr, runner=runner, gpu="t4", spawn_keepalive=False)
    try:
        assert session.owns_connection is True
        assert colab_client.assign_calls == 1
        assert store.load() is not None
    finally:
        session.close()


def test_colab_attaches_to_existing_connection(tmp_path: Path, runner) -> None:
    mgr, _, _, _, colab_client, _ = build_manager(tmp_path, seed=seed_connection())

    session = colab(manager=mgr, runner=runner, gpu="t4")
    try:
        assert session.owns_connection is False
        assert colab_client.assign_calls == 0
    finally:
        session.close()


def test_exit_disconnects_only_when_owned(tmp_path: Path, runner) -> None:
    mgr, _, _, _, colab_client, store = build_manager(tmp_path)

    with colab(manager=mgr, runner=runner, gpu="t4", spawn_keepalive=False):
        pass

    assert colab_client.unassigned == ["ep"]
    assert store.load() is None


def test_exit_leaves_attached_connection_alone(tmp_path: Path, runner) -> None:
    mgr, _, _, _, colab_client, store = build_manager(
        tmp_path, seed=seed_connection()
    )

    with colab(manager=mgr, runner=runner, gpu="t4"):
        pass

    assert colab_client.unassigned == []
    assert store.load() is not None


def test_require_accelerator_match_raises_on_mismatch(tmp_path: Path, runner) -> None:
    mgr, *_ = build_manager(tmp_path, seed=seed_connection(accelerator="A100"))

    with pytest.raises(ConnectionError):
        colab(
            manager=mgr,
            runner=runner,
            gpu="t4",
            require_accelerator_match=True,
        )


def test_attach_only_raises_when_no_connection(tmp_path: Path, runner) -> None:
    mgr, *_ = build_manager(tmp_path)
    with pytest.raises(ConnectionError):
        colab(manager=mgr, runner=runner, gpu="t4", attach_only=True)


# ---------------------------------------------------------------- run tests


def test_session_run_returns_run_result_and_does_not_raise_on_remote_error(
    tmp_path: Path, runner
) -> None:
    fs = FakeFS()

    class ErrKernel(FakeKernel):
        async def execute(self, code, **_):
            return CellResult(
                index=0, source=code, status="error", error="oops", traceback=["tb"]
            )

    mgr, *_ = build_manager(
        tmp_path, seed=seed_connection(), fs=fs, kernel=ErrKernel(fs)
    )
    session = colab(manager=mgr, runner=runner)
    try:
        result = session.run("raise ValueError('x')")
        assert result.status == "error"
        assert result.error == "oops"
    finally:
        session.close()


# ---------------------------------------------------------- decorator tests


def _add(a, b):
    return a + b


def test_remote_decorator_happy_path(tmp_path: Path, runner) -> None:
    mgr, *_ = build_manager(tmp_path, seed=seed_connection())
    session = ColabSession(manager=mgr, owns_connection=False, runner=runner)

    @remote(session=session)
    def add(a, b):
        return _add(a, b)

    assert add(40, 2) == 42


def test_remote_decorator_reraises_remote_exception(tmp_path: Path, runner) -> None:
    fs = FakeFS()
    remote_exc = ValueError("boom")
    kernel = FakeKernel(
        fs,
        hardcoded_payload=("err", remote_exc, ["tb-line-1", "tb-line-2"]),
    )
    mgr, *_ = build_manager(tmp_path, seed=seed_connection(), fs=fs, kernel=kernel)
    session = ColabSession(manager=mgr, owns_connection=False, runner=runner)

    @remote(session=session)
    def bad():
        raise AssertionError("never called")

    with pytest.raises(RemoteExecutionError) as exc_info:
        bad()
    err = exc_info.value
    assert err.remote_traceback == ["tb-line-1", "tb-line-2"]
    assert isinstance(err.__cause__, ValueError)
    assert str(err.__cause__) == "boom"


def test_remote_decorator_raises_when_harness_crashes(tmp_path: Path, runner) -> None:
    fs = FakeFS()
    kernel = FakeKernel(fs, harness_status="error")
    mgr, *_ = build_manager(tmp_path, seed=seed_connection(), fs=fs, kernel=kernel)
    session = ColabSession(manager=mgr, owns_connection=False, runner=runner)

    @remote(session=session)
    def whatever():
        return None

    with pytest.raises(RemoteExecutionError):
        whatever()


def test_remote_decorator_does_not_touch_manager_when_session_passed(
    tmp_path: Path, runner
) -> None:
    mgr, *_, colab_client, _ = build_manager(tmp_path, seed=seed_connection())
    session = ColabSession(manager=mgr, owns_connection=False, runner=runner)

    @remote(session=session)
    def identity(x):
        return x

    assert identity("hello") == "hello"
    assert colab_client.assign_calls == 0
    assert colab_client.unassigned == []


# ----------------------------------------------------------- SyncRunner tests


def test_sync_runner_concurrent_run_from_multiple_threads() -> None:
    r = SyncRunner()
    try:
        async def add(x, y):
            await asyncio.sleep(0.01)
            return x + y

        results: list[int | None] = [None, None]

        def worker(i: int, a: int, b: int) -> None:
            results[i] = r.run(add(a, b))

        t1 = threading.Thread(target=worker, args=(0, 1, 2))
        t2 = threading.Thread(target=worker, args=(1, 3, 4))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert results == [3, 7]
    finally:
        r.close()


def test_sync_runner_works_from_inside_a_running_event_loop() -> None:
    r = SyncRunner()
    try:

        async def inner():
            return 42

        async def outer():
            loop = asyncio.get_running_loop()
            # Dispatch to a thread so we do not block the caller's loop while
            # r.run() waits. This mimics what a Jupyter user would do.
            return await loop.run_in_executor(None, r.run, inner())

        assert asyncio.run(outer()) == 42
    finally:
        r.close()


def test_reset_runner_allows_fresh_runner_after_shutdown() -> None:
    from colab_cli.api._sync import get_runner

    r1 = get_runner()

    async def ping():
        return "pong"

    assert r1.run(ping()) == "pong"

    reset_runner()
    r2 = get_runner()
    assert r2 is not r1
    assert r2.run(ping()) == "pong"
