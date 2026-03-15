"""Authentication-related CLI commands."""

from __future__ import annotations

import typer

from colab_cli.config import load_app_config
from colab_cli.core.auth.credentials import CredentialManager
from colab_cli.core.auth.oauth import fetch_user_info, run_oauth_login
from colab_cli.formats.output import format_json


def register(app: typer.Typer) -> None:
    @app.command("login")
    def login(no_browser: bool = typer.Option(False, "--no-browser", help="Do not open a browser automatically.")) -> None:
        config = load_app_config()
        manager = CredentialManager(config=config)
        token = run_oauth_login(config, open_browser=not no_browser)
        manager.save_token(token)
        user = fetch_user_info(token.access_token)
        typer.echo(f"Logged in as {user.email or user.name or 'unknown user'}", err=True)

    @app.command("logout")
    def logout() -> None:
        config = load_app_config()
        manager = CredentialManager(config=config)
        manager.clear()
        typer.echo("Stored login cleared.", err=True)

    @app.command("whoami")
    def whoami(as_json: bool = typer.Option(False, "--json", help="Emit JSON output.")) -> None:
        config = load_app_config()
        manager = CredentialManager(config=config)
        token = manager.get_valid_token()
        user = fetch_user_info(token.access_token)
        if as_json:
            typer.echo(format_json(user))
            return
        typer.echo(user.email or user.name or "unknown user")

