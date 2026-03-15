"""OAuth login helpers."""

from __future__ import annotations

import os
from datetime import UTC

import httpx
from google_auth_oauthlib.flow import InstalledAppFlow

# Google normalizes short scope aliases (e.g. "profile" → "googleapis.com/auth/userinfo.profile")
# in token responses. oauthlib treats this as a scope change and raises an error. Relax this check.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from colab_cli.models import AppConfig, TokenData, UserInfo

GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def build_client_config(config: AppConfig) -> dict[str, dict[str, object]]:
    return {
        "installed": {
            "client_id": config.oauth.client_id,
            "client_secret": config.oauth.client_secret,
            "auth_uri": str(config.oauth.auth_uri),
            "token_uri": str(config.oauth.token_uri),
            "redirect_uris": [
                "http://127.0.0.1",
                "http://localhost",
            ],
        }
    }


def run_oauth_login(
    config: AppConfig,
    *,
    open_browser: bool = True,
    port: int = 0,
) -> TokenData:
    flow = InstalledAppFlow.from_client_config(
        build_client_config(config),
        scopes=list(config.oauth.scopes),
    )
    credentials = flow.run_local_server(
        host="127.0.0.1",
        port=port,
        open_browser=open_browser,
        authorization_prompt_message="Opening browser for Colab OAuth login...",
        success_message="Authentication complete. You may close this window.",
        access_type="offline",
        prompt="consent",
    )
    return TokenData(
        access_token=credentials.token,
        refresh_token=credentials.refresh_token,
        expires_at=credentials.expiry.astimezone(UTC) if credentials.expiry else None,
        scope=" ".join(credentials.scopes or config.oauth.scopes),
        token_type="Bearer",
    )


def fetch_user_info(
    access_token: str,
    *,
    client: httpx.Client | None = None,
) -> UserInfo:
    owns_client = client is None
    http = client or httpx.Client(timeout=30.0)
    try:
        response = http.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        return UserInfo.model_validate(response.json())
    finally:
        if owns_client:
            http.close()

