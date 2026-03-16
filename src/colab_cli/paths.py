"""Filesystem path helpers for colab-cli state."""

from __future__ import annotations

from pathlib import Path

APP_DIRNAME = "colab-cli"


def app_config_dir(home: Path | None = None) -> Path:
    base_home = home or Path.home()
    return base_home / ".config" / APP_DIRNAME


def config_file_path(home: Path | None = None) -> Path:
    return app_config_dir(home) / "config.toml"


def token_file_path(home: Path | None = None) -> Path:
    return app_config_dir(home) / "token.json"


def active_connection_path(home: Path | None = None) -> Path:
    return app_config_dir(home) / "active.json"


def ensure_app_config_dir(home: Path | None = None) -> Path:
    directory = app_config_dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    return directory

