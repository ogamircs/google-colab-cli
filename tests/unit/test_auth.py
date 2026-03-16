from __future__ import annotations

import os
import stat
from datetime import UTC, datetime, timedelta

import httpx

from colab_cli.core.auth.credentials import CredentialManager
from colab_cli.core.auth.oauth import fetch_user_info
from colab_cli.core.auth.token_store import TokenStore
from colab_cli.models import AppConfig, OAuthConfig, TokenData


def make_config() -> AppConfig:
    return AppConfig(
        oauth=OAuthConfig(
            client_id="client.apps.googleusercontent.com",
            client_secret="secret",
        )
    )


def test_credential_manager_uses_existing_token_when_fresh(tmp_path) -> None:
    manager = CredentialManager(config=make_config(), home=tmp_path)
    token = TokenData(
        access_token="fresh-token",
        refresh_token="refresh-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scope="openid email profile",
        token_type="Bearer",
    )
    manager.token_store.save(token)

    resolved = manager.get_valid_token()

    assert resolved.access_token == "fresh-token"


def test_credential_manager_refreshes_expiring_token(tmp_path) -> None:
    manager = CredentialManager(
        config=make_config(),
        home=tmp_path,
        refresh_fn=lambda _config, _token: TokenData(
            access_token="refreshed-token",
            refresh_token="refresh-token",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scope="openid email profile",
            token_type="Bearer",
        ),
    )
    manager.token_store.save(
        TokenData(
            access_token="old-token",
            refresh_token="refresh-token",
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
            scope="openid email profile",
            token_type="Bearer",
        )
    )

    resolved = manager.get_valid_token()

    assert resolved.access_token == "refreshed-token"
    assert manager.token_store.load().access_token == "refreshed-token"


def test_fetch_user_info_parses_google_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer access-token"
        return httpx.Response(
            200,
            json={
                "sub": "123",
                "email": "user@example.com",
                "name": "Example User",
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    try:
        user_info = fetch_user_info("access-token", client=client)
    finally:
        client.close()

    assert user_info.email == "user@example.com"


def test_token_store_saves_tokens_with_owner_only_permissions(tmp_path) -> None:
    store = TokenStore(home=tmp_path)
    token = TokenData(
        access_token="fresh-token",
        refresh_token="refresh-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scope="openid email profile",
        token_type="Bearer",
    )
    original_umask = os.umask(0)
    try:
        store.save(token)
    finally:
        os.umask(original_umask)

    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
