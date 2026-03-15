"""Persistence for active Colab runtime state."""

from __future__ import annotations

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
        self.path.write_text(connection.model_dump_json(indent=2))

    def delete(self) -> None:
        if self.path.exists():
            self.path.unlink()
