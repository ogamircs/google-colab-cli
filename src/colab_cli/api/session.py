"""Python-level session handle over a Colab runtime.

Wraps the async :class:`RuntimeManager` with a synchronous facade suitable
for use inside regular Python scripts, Jupyter notebooks, and async code.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from colab_cli.core.runtime import RuntimeManager, create_runtime_manager
from colab_cli.errors import ConnectionError
from colab_cli.models import JupyterContent, RunResult, StatusResult

from ._sync import SyncRunner, get_runner

if TYPE_CHECKING:
    from collections.abc import Callable as _Callable  # noqa: F401


class ColabSession:
    """Synchronous handle to an active Colab runtime.

    Prefer constructing via :func:`colab`. A session is also a context
    manager — entering is a no-op (connection already established by
    :func:`colab`), and exit disconnects only if this session owns the
    underlying runtime.
    """

    def __init__(
        self,
        *,
        manager: RuntimeManager,
        owns_connection: bool,
        runner: SyncRunner,
        spawn_keepalive: bool = False,
    ) -> None:
        self._manager = manager
        self._owns_connection = owns_connection
        self._runner = runner
        self._spawn_keepalive = spawn_keepalive
        self._closed = False

    # ------------------------------------------------------------------ props

    @property
    def owns_connection(self) -> bool:
        return self._owns_connection

    @property
    def status(self) -> StatusResult:
        return self._manager.status()

    @property
    def accelerator(self) -> str | None:
        return self.status.accelerator

    @property
    def manager(self) -> RuntimeManager:
        return self._manager

    # ---------------------------------------------------------------- lifecyc

    def __enter__(self) -> "ColabSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._owns_connection:
            return
        try:
            self._runner.run(self._manager.disconnect())
        except Exception:
            # Runtime may already be gone (reclaimed, network dropped); do
            # not mask the caller's original exception.
            pass

    # ---------------------------------------------------------------- execute

    def run(
        self,
        code: str,
        *,
        source_name: str = "api.py",
        secrets: dict[str, str] | None = None,
        on_stream: Callable[[str, str], Any] | None = None,
        allow_stdin: bool = False,
        timeout: float | None = None,
    ) -> RunResult:
        """Execute Python code on the runtime and return the :class:`RunResult`.

        Never raises on remote code errors — inspect ``result.status``.
        Raises ``AuthError``/``ConnectionError``/``ColabRuntimeError`` for
        transport-level failures.
        """
        return self._runner.run(
            self._manager.run_code(
                code,
                source_name=source_name,
                allow_stdin=allow_stdin,
                on_stream=on_stream,
                secrets=secrets,
            ),
            timeout=timeout,
        )

    # -------------------------------------------------------------------- fs

    def push(self, local: str | Path, remote: str) -> None:
        self._runner.run(self._manager.push_file(Path(local), remote))

    def pull(self, remote: str, local: str | Path) -> Path:
        return self._runner.run(self._manager.pull_file(remote, Path(local)))

    def ls(self, remote: str = "") -> list[JupyterContent]:
        return self._runner.run(self._manager.list_files(remote))


def colab(
    *,
    gpu: str | None = "t4",
    authuser: int | None = None,
    spawn_keepalive: bool | None = None,
    attach_only: bool = False,
    require_accelerator_match: bool = False,
    manager: RuntimeManager | None = None,
    home: Path | None = None,
    runner: SyncRunner | None = None,
) -> ColabSession:
    """Return a :class:`ColabSession` bound to an active Colab runtime.

    If a runtime is already active (e.g. one created via ``colab connect``),
    attach to it without touching its lifecycle. Otherwise, allocate a new
    runtime with the requested accelerator — this call owns it and will
    disconnect it when the session closes.

    Parameters
    ----------
    gpu:
        Accelerator name (``"t4"``, ``"v100"``, ``"a100"``) or ``None`` for CPU.
    spawn_keepalive:
        When this call owns the connection, whether to spawn the background
        keepalive subprocess. ``None`` → spawn. ``False`` → skip (short-lived
        CI / ephemeral callers).
    attach_only:
        If True, raise ``ConnectionError`` when no runtime is active instead
        of auto-connecting.
    require_accelerator_match:
        If True and an existing runtime's accelerator differs from ``gpu``,
        raise ``ConnectionError`` instead of silently attaching.
    manager, home, runner:
        Injection points for testing.
    """
    runner = runner or get_runner()
    mgr = manager or create_runtime_manager(
        home=home,
        spawn_keepalive=False,
        allow_missing_config=False,
    )

    st = mgr.status()
    owns = False

    if st.connected:
        if require_accelerator_match:
            wanted = gpu.upper() if gpu else None
            actual = st.accelerator or None
            if wanted != actual:
                raise ConnectionError(
                    f"Existing runtime has accelerator={actual!r} but "
                    f"{wanted!r} was requested. Disconnect first or pass "
                    "require_accelerator_match=False to attach anyway."
                )
    else:
        if attach_only:
            raise ConnectionError(
                "No active Colab runtime. Run `colab connect` first or call "
                "colab(attach_only=False)."
            )
        want_keepalive = True if spawn_keepalive is None else spawn_keepalive
        mgr._spawn_keepalive = want_keepalive
        runner.run(mgr.connect(accelerator=gpu, authuser=authuser))
        owns = True

    return ColabSession(
        manager=mgr,
        owns_connection=owns,
        runner=runner,
        spawn_keepalive=bool(getattr(mgr, "_spawn_keepalive", False)),
    )
