"""Authentication-related CLI commands."""

from __future__ import annotations

import json
from datetime import timedelta

import typer

from colab_cli.config import load_app_config
from colab_cli.core.auth.credentials import CredentialManager
from colab_cli.core.auth.oauth import fetch_user_info, run_oauth_login
from colab_cli.formats.output import format_json
from colab_cli.utils import should_refresh_soon, utc_now

auth_app = typer.Typer(no_args_is_help=True)


@auth_app.command("login")
def login(no_browser: bool = typer.Option(False, "--no-browser", help="Do not open a browser automatically.")) -> None:
    config = load_app_config()
    manager = CredentialManager(config=config)
    token = run_oauth_login(config, open_browser=not no_browser)
    manager.save_token(token)
    user = fetch_user_info(token.access_token)
    typer.echo(f"Logged in as {user.email or user.name or 'unknown user'}", err=True)


@auth_app.command("logout")
def logout() -> None:
    config = load_app_config()
    manager = CredentialManager(config=config)
    manager.clear()
    typer.echo("Stored login cleared.", err=True)


@auth_app.command("whoami")
def whoami(as_json: bool = typer.Option(False, "--json", help="Emit JSON output.")) -> None:
    config = load_app_config()
    manager = CredentialManager(config=config)
    token = manager.get_valid_token()
    user = fetch_user_info(token.access_token)
    if as_json:
        typer.echo(format_json(user))
        return
    typer.echo(user.email or user.name or "unknown user")


@auth_app.command("status")
def status(as_json: bool = typer.Option(False, "--json", help="Emit JSON output.")) -> None:
    """Check authentication state without triggering login flows."""
    config = load_app_config()
    manager = CredentialManager(config=config)
    token = manager.token_store.load()

    authenticated = False
    email: str | None = None
    expires_at: str | None = None

    if token is not None and not should_refresh_soon(token.expires_at, threshold=timedelta(0)):
        authenticated = True
        expires_at = token.expires_at.isoformat() if token.expires_at else None
        try:
            user = fetch_user_info(token.access_token)
            email = user.email
        except Exception:
            pass

    if as_json:
        typer.echo(json.dumps({"authenticated": authenticated, "email": email, "expires_at": expires_at}))
    elif authenticated:
        label = email or "unknown user"
        typer.echo(f"Authenticated as {label}")
    else:
        typer.echo("Not authenticated")
