"""Application configuration loading."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from colab_cli.errors import ConfigError
from colab_cli.models import AppConfig, OAuthConfig
from colab_cli.paths import config_file_path

DEFAULT_SCOPES = (
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/colaboratory",
)


def load_app_config(
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> AppConfig:
    merged_env = dict(os.environ if env is None else env)
    file_config = _load_file_config(home=home)

    oauth_file = file_config.get("oauth", {})
    client_id = merged_env.get("COLAB_CLIENT_ID") or oauth_file.get("client_id")
    client_secret = merged_env.get("COLAB_CLIENT_SECRET") or oauth_file.get("client_secret")
    default_accelerator = (
        merged_env.get("COLAB_DEFAULT_ACCELERATOR")
        or file_config.get("default_accelerator")
        or oauth_file.get("default_accelerator")
    )

    if not client_id or not client_secret:
        raise ConfigError(
            "Missing OAuth client credentials. Set COLAB_CLIENT_ID/COLAB_CLIENT_SECRET "
            "or create ~/.config/colab-cli/config.toml."
        )

    return AppConfig(
        oauth=OAuthConfig(
            client_id=client_id,
            client_secret=client_secret,
            auth_uri=oauth_file.get("auth_uri", "https://accounts.google.com/o/oauth2/auth"),
            token_uri=oauth_file.get("token_uri", "https://oauth2.googleapis.com/token"),
            scopes=tuple(oauth_file.get("scopes", DEFAULT_SCOPES)),
        ),
        default_accelerator=default_accelerator,
        default_authuser=int(merged_env.get("COLAB_AUTHUSER", file_config.get("default_authuser", 0))),
    )


def _load_file_config(*, home: Path | None = None) -> dict[str, Any]:
    path = config_file_path(home)
    if not path.exists():
        return {}

    try:
        with path.open("rb") as handle:
            loaded = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid config file: {path}") from exc

    if not isinstance(loaded, dict):
        raise ConfigError(f"Unexpected config structure in {path}")
    return loaded
