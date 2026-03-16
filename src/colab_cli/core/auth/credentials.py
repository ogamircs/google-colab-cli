"""Credential refresh and persistence."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from colab_cli.errors import AuthError
from colab_cli.models import AppConfig, TokenData
from colab_cli.utils import should_refresh_soon, utc_now

from .token_store import TokenStore

RefreshFn = Callable[[AppConfig, TokenData], TokenData]


class CredentialManager:
    def __init__(
        self,
        *,
        config: AppConfig,
        home: Path | None = None,
        token_store: TokenStore | None = None,
        refresh_fn: RefreshFn | None = None,
    ) -> None:
        self.config = config
        self.token_store = token_store or TokenStore(home=home)
        self._refresh_fn = refresh_fn or refresh_token

    def load_token(self) -> TokenData:
        token = self.token_store.load()
        if token is None:
            raise AuthError("No stored login found. Run `colab auth login` first.")
        return token

    def get_valid_token(self) -> TokenData:
        token = self.load_token()
        if should_refresh_soon(token.expires_at, now=utc_now()):
            if not token.refresh_token:
                raise AuthError("Stored token is expired and does not include a refresh token.")
            token = self._refresh_fn(self.config, token)
            self.token_store.save(token)
        return token

    def get_access_token(self) -> str:
        return self.get_valid_token().access_token

    def save_token(self, token: TokenData) -> None:
        self.token_store.save(token)

    def clear(self) -> None:
        self.token_store.delete()


def refresh_token(config: AppConfig, token: TokenData) -> TokenData:
    credentials = Credentials(
        token=token.access_token,
        refresh_token=token.refresh_token,
        token_uri=str(config.oauth.token_uri),
        client_id=config.oauth.client_id,
        client_secret=config.oauth.client_secret,
        scopes=list(config.oauth.scopes),
    )
    credentials.refresh(Request())
    return TokenData(
        access_token=credentials.token,
        refresh_token=credentials.refresh_token or token.refresh_token,
        expires_at=credentials.expiry,
        scope=" ".join(credentials.scopes or config.oauth.scopes),
        token_type="Bearer",
        issued_at=utc_now(),
    )
