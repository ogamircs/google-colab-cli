"""@remote decorator — run a Python function on a Colab runtime.

Args and return values cross the wire as cloudpickle envelopes staged
through the Jupyter contents API. Remote exceptions are unpickled locally
and re-raised as :class:`RemoteExecutionError` with the original exception
chained as ``__cause__``.
"""

from __future__ import annotations

import functools
import secrets as _secrets_mod
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar, overload

import cloudpickle

from colab_cli.errors import ColabCliError, RemoteExecutionError

from ._harness import DONE_MARKER, render_harness
from .session import ColabSession, colab as _colab

F = TypeVar("F", bound=Callable[..., Any])


@overload
def remote(_fn: F) -> F: ...


@overload
def remote(
    *,
    gpu: str | None = "t4",
    session: ColabSession | None = None,
    secrets: dict[str, str] | None = None,
    timeout: float | None = None,
    remote_staging_dir: str = "/content/.colab_cli",
) -> Callable[[F], F]: ...


def remote(
    _fn: Callable[..., Any] | None = None,
    *,
    gpu: str | None = "t4",
    session: ColabSession | None = None,
    secrets: dict[str, str] | None = None,
    timeout: float | None = None,
    remote_staging_dir: str = "/content/.colab_cli",
) -> Any:
    """Decorator that runs the wrapped function on a Colab runtime.

    Usage::

        @remote(gpu="t4")
        def train(x, y):
            import torch
            return (torch.tensor(x).cuda() @ torch.tensor(y).cuda()).cpu().tolist()

    If ``session`` is provided, the decorator reuses it (no connect/disconnect
    per call). Otherwise a short-lived :func:`colab` session is opened per
    call.
    """

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if session is not None:
                return _invoke_remote(
                    session,
                    fn,
                    args,
                    kwargs,
                    secrets=secrets,
                    timeout=timeout,
                    staging_dir=remote_staging_dir,
                )
            with _colab(gpu=gpu) as s:
                return _invoke_remote(
                    s,
                    fn,
                    args,
                    kwargs,
                    secrets=secrets,
                    timeout=timeout,
                    staging_dir=remote_staging_dir,
                )

        return wrapper

    if _fn is not None:
        return decorate(_fn)
    return decorate


def _invoke_remote(
    session: ColabSession,
    fn: Callable[..., Any],
    args: tuple,
    kwargs: dict,
    *,
    secrets: dict[str, str] | None,
    timeout: float | None,
    staging_dir: str,
) -> Any:
    slug = _secrets_mod.token_hex(8)
    slug_dir = f"{staging_dir.rstrip('/')}/{slug}"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        fn_local = tmp_path / "fn.pkl"
        arg_local = tmp_path / "args.pkl"
        fn_local.write_bytes(cloudpickle.dumps(fn))
        arg_local.write_bytes(cloudpickle.dumps((args, kwargs)))

        # Stage dir + files
        stage_result = session.run(
            f"import os; os.makedirs({slug_dir!r}, exist_ok=True)",
            source_name="api_stage.py",
        )
        if stage_result.status == "error":
            raise RemoteExecutionError(
                f"Could not create staging dir on runtime: {stage_result.error}",
                error=stage_result.error,
                remote_traceback=stage_result.traceback or [],
                stdout=stage_result.stdout,
                stderr=stage_result.stderr,
            )
        session.push(fn_local, f"{slug_dir}/fn.pkl")
        session.push(arg_local, f"{slug_dir}/args.pkl")

        # Run harness
        harness_code = render_harness(slug_dir)
        result = session.run(
            harness_code,
            source_name="api_harness.py",
            secrets=secrets,
            timeout=timeout,
        )
        if result.status == "error":
            raise RemoteExecutionError(
                f"Harness crashed on Colab: {result.error}",
                error=result.error,
                remote_traceback=result.traceback or [],
                stdout=result.stdout,
                stderr=result.stderr,
            )
        if f"{DONE_MARKER}:{slug_dir}/result.pkl" not in result.stdout:
            raise RemoteExecutionError(
                "Harness completed without writing a result envelope.",
                stdout=result.stdout,
                stderr=result.stderr,
            )

        # Pull + unpickle
        result_local = tmp_path / "result.pkl"
        session.pull(f"{slug_dir}/result.pkl", result_local)
        try:
            envelope = cloudpickle.loads(result_local.read_bytes())
        except Exception as exc:  # pickle errors, truncated reads, etc.
            raise RemoteExecutionError(
                f"Failed to unpickle remote result: {exc}",
                stdout=result.stdout,
                stderr=result.stderr,
            ) from exc

    # Best-effort remote cleanup — don't mask real errors
    try:
        session.run(
            f"import shutil; shutil.rmtree({slug_dir!r}, ignore_errors=True)",
            source_name="api_cleanup.py",
        )
    except ColabCliError:
        pass

    if not (isinstance(envelope, tuple) and envelope):
        raise RemoteExecutionError(
            f"Unexpected result envelope shape: {envelope!r}",
            stdout=result.stdout,
            stderr=result.stderr,
        )

    kind = envelope[0]
    if kind == "ok":
        return envelope[1]
    if kind == "err":
        remote_exc = envelope[1]
        tb_list = list(envelope[2]) if len(envelope) > 2 else []
        err = RemoteExecutionError(
            f"Remote function raised {type(remote_exc).__name__}: {remote_exc}",
            error=f"{type(remote_exc).__name__}: {remote_exc}",
            remote_traceback=tb_list,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        raise err from remote_exc
    raise RemoteExecutionError(
        f"Unknown envelope kind: {kind!r}",
        stdout=result.stdout,
        stderr=result.stderr,
    )
