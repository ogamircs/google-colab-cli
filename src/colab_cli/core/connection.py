"""Persistence for active Colab runtime state."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from colab_cli.models import ActiveConnection
from colab_cli.paths import active_connection_path, ensure_app_config_dir

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None  # type: ignore[assignment]


class ConnectionStore:
    def __init__(self, *, home: Path | None = None) -> None:
        self._home = home

    @property
    def path(self) -> Path:
        return active_connection_path(self._home)

    def load(self) -> ActiveConnection | None:
        if not self.path.exists():
            return None
        return ActiveConnection.model_validate_json(self.path.read_text())

    def save(self, connection: ActiveConnection) -> None:
        with self._locked():
            self._write(connection)

    def update(
        self, mutate: Callable[[ActiveConnection], ActiveConnection | None]
    ) -> ActiveConnection | None:
        """Locked read-modify-write against the latest on-disk state.

        The keepalive subprocess and the main process both persist connection
        state; mutating a fresh copy under the lock prevents either from
        clobbering fields the other wrote (e.g. session_id vs last_keepalive_at).
        Returns the persisted connection, or None when no connection exists.
        """
        with self._locked():
            connection = self.load()
            if connection is None:
                return None
            connection = mutate(connection) or connection
            self._write(connection)
            return connection

    def delete(self) -> None:
        with self._locked():
            if self.path.exists():
                self.path.unlink()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        ensure_app_config_dir(self._home)
        if fcntl is None:  # pragma: no cover - Windows has no fcntl
            yield
            return
        fd = os.open(self.path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _write(self, connection: ActiveConnection) -> None:
        # Temp file + os.replace so readers never observe partial JSON.
        # mkstemp creates the file 0600 regardless of umask.
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(connection.model_dump_json(indent=2))
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
