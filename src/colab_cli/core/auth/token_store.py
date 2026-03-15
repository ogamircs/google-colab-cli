"""Persistence for OAuth token state."""

from __future__ import annotations

from pathlib import Path

from colab_cli.models import TokenData
from colab_cli.paths import ensure_app_config_dir, token_file_path


class TokenStore:
    def __init__(self, *, home: Path | None = None) -> None:
        self._home = home

    @property
    def path(self) -> Path:
        return token_file_path(self._home)

    def load(self) -> TokenData | None:
        if not self.path.exists():
            return None
        return TokenData.model_validate_json(self.path.read_text())

    def save(self, token: TokenData) -> None:
        ensure_app_config_dir(self._home)
        self.path.write_text(token.model_dump_json(indent=2))

    def delete(self) -> None:
        if self.path.exists():
            self.path.unlink()

