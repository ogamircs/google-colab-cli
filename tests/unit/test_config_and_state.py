from __future__ import annotations

import os
import stat
from datetime import UTC, datetime, timedelta

import pytest

from colab_cli.config import load_app_config
from colab_cli.core.auth.token_store import TokenStore
from colab_cli.core.connection import ConnectionStore
from colab_cli.errors import ConfigError
from colab_cli.models import ActiveConnection, TokenData


def test_load_app_config_from_file(tmp_path) -> None:
    config_dir = tmp_path / ".config" / "colab-cli"
    config_dir.mkdir(parents=True)
    config_dir.joinpath("config.toml").write_text(
        """
[oauth]
client_id = "file-client.apps.googleusercontent.com"
client_secret = "file-secret"
""".strip()
    )

    config = load_app_config(home=tmp_path, env={})

    assert config.oauth.client_id == "file-client.apps.googleusercontent.com"
    assert config.oauth.client_secret == "file-secret"


def test_load_app_config_prefers_environment(tmp_path) -> None:
    config_dir = tmp_path / ".config" / "colab-cli"
    config_dir.mkdir(parents=True)
    config_dir.joinpath("config.toml").write_text(
        """
[oauth]
client_id = "file-client.apps.googleusercontent.com"
client_secret = "file-secret"

default_accelerator = "t4"
""".strip()
    )

    env = {
        "COLAB_CLIENT_ID": "env-client.apps.googleusercontent.com",
        "COLAB_CLIENT_SECRET": "env-secret",
        "COLAB_DEFAULT_ACCELERATOR": "a100",
    }
    config = load_app_config(home=tmp_path, env=env)

    assert config.oauth.client_id == "env-client.apps.googleusercontent.com"
    assert config.oauth.client_secret == "env-secret"
    assert config.default_accelerator == "a100"


def test_load_app_config_errors_when_missing(tmp_path) -> None:
    with pytest.raises(ConfigError):
        load_app_config(home=tmp_path, env={})


def test_token_store_round_trip(tmp_path) -> None:
    store = TokenStore(home=tmp_path)
    token = TokenData(
        access_token="access",
        refresh_token="refresh",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scope="openid",
        token_type="Bearer",
    )

    store.save(token)

    assert store.load() == token


def test_token_store_delete_clears_file(tmp_path) -> None:
    store = TokenStore(home=tmp_path)
    store.save(
        TokenData(
            access_token="access",
            refresh_token="refresh",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scope="openid",
            token_type="Bearer",
        )
    )

    store.delete()

    assert store.load() is None


def test_connection_store_round_trip(tmp_path) -> None:
    store = ConnectionStore(home=tmp_path)
    connection = ActiveConnection(
        notebook_hash="hash",
        endpoint_id="endpoint",
        proxy_url="https://proxy.example.com",
        proxy_token="proxy-token",
        proxy_expires_at=datetime.now(UTC) + timedelta(hours=1),
        accelerator="T4",
        authuser=0,
    )

    store.save(connection)

    assert store.load() == connection


def test_connection_store_delete_clears_file(tmp_path) -> None:
    store = ConnectionStore(home=tmp_path)
    store.save(
        ActiveConnection(
            notebook_hash="hash",
            endpoint_id="endpoint",
            proxy_url="https://proxy.example.com",
            proxy_token="proxy-token",
            proxy_expires_at=datetime.now(UTC) + timedelta(hours=1),
            accelerator="T4",
            authuser=0,
        )
    )

    store.delete()

    assert store.load() is None


def test_connection_store_saves_active_state_with_owner_only_permissions(tmp_path) -> None:
    store = ConnectionStore(home=tmp_path)
    connection = ActiveConnection(
        notebook_hash="hash",
        endpoint_id="endpoint",
        proxy_url="https://proxy.example.com",
        proxy_token="proxy-token",
        proxy_expires_at=datetime.now(UTC) + timedelta(hours=1),
        accelerator="T4",
        authuser=0,
    )
    original_umask = os.umask(0)
    try:
        store.save(connection)
    finally:
        os.umask(original_umask)

    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
