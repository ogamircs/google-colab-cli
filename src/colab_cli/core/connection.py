"""Persistence for active Colab runtime state."""

from __future__ import annotations

import os
from pathlib import Path

from colab_cli.models import ActiveConnection
from colab_cli.paths import active_connection_path, ensure_app_config_dir


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
        ensure_app_config_dir(self._home)
        payload = connection.model_dump_json(indent=2)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        self.path.chmod(0o600)

    def delete(self) -> None:
        if self.path.exists():
            self.path.unlink()
