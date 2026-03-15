from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from colab_cli.models import ActiveConnection, OAuthConfig, TokenData
from colab_cli.paths import (
    active_connection_path,
    app_config_dir,
    config_file_path,
    token_file_path,
)
from colab_cli.utils import generate_notebook_hash, should_refresh_soon, strip_xssi_prefix


def test_config_paths_use_supplied_home_directory() -> None:
    home = Path("/tmp/example-home")

    assert app_config_dir(home) == home / ".config" / "colab-cli"
    assert config_file_path(home) == home / ".config" / "colab-cli" / "config.toml"
    assert token_file_path(home) == home / ".config" / "colab-cli" / "token.json"
    assert active_connection_path(home) == home / ".config" / "colab-cli" / "active.json"


def test_strip_xssi_prefix_removes_colab_prefix() -> None:
    payload = ")]}'\n{\"hello\": \"world\"}"

    assert strip_xssi_prefix(payload) == "{\"hello\": \"world\"}"


def test_generate_notebook_hash_is_colab_safe() -> None:
    notebook_hash = generate_notebook_hash()

    assert len(notebook_hash) == 44
    assert "-" not in notebook_hash
    assert "_" in notebook_hash


def test_should_refresh_soon_checks_threshold() -> None:
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)

    assert should_refresh_soon(now + timedelta(minutes=4), now=now) is True
    assert should_refresh_soon(now + timedelta(minutes=6), now=now) is False


def test_models_round_trip_datetime_fields() -> None:
    issued_at = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
    expires_at = issued_at + timedelta(hours=1)
    token = TokenData(
        access_token="access",
        refresh_token="refresh",
        expires_at=expires_at,
        scope="scope-a scope-b",
        token_type="Bearer",
        issued_at=issued_at,
    )

    reloaded = TokenData.model_validate_json(token.model_dump_json())

    assert reloaded == token


def test_active_connection_requires_proxy_expiry() -> None:
    with pytest.raises(ValueError):
        ActiveConnection(
            notebook_hash="abc",
            endpoint_id="endpoint",
            proxy_url="https://example.com",
            proxy_token="proxy",
            proxy_expires_at=None,
            accelerator="T4",
        )


def test_oauth_config_requires_client_credentials() -> None:
    config = OAuthConfig(
        client_id="client-id.apps.googleusercontent.com",
        client_secret="secret",
    )

    assert config.client_id.startswith("client-id")
