"""Connection lifecycle CLI commands."""

from __future__ import annotations

import asyncio
import time

import typer

from colab_cli.core.runtime import create_runtime_manager
from colab_cli.formats.output import format_human_status, format_json


def register(app: typer.Typer) -> None:
    @app.command("connect")
    def connect(
        gpu: str | None = typer.Option(None, "--gpu", help="Request a GPU accelerator such as t4, v100, or a100."),
    ) -> None:
        typer.echo("Connecting to Colab runtime...", err=True)
        status = asyncio.run(create_runtime_manager().connect(accelerator=gpu))
        typer.echo(format_human_status(status))

    @app.command("status")
    def status(as_json: bool = typer.Option(False, "--json", help="Emit JSON output.")) -> None:
        result = create_runtime_manager(spawn_keepalive=False, allow_missing_config=True).status()
        if as_json:
            typer.echo(format_json(result))
            return
        typer.echo(format_human_status(result))

    @app.command("disconnect")
    def disconnect() -> None:
        typer.echo("Disconnecting runtime...", err=True)
        status = asyncio.run(create_runtime_manager(spawn_keepalive=False, allow_missing_config=True).disconnect())
        typer.echo(format_human_status(status))

    @app.command("_internal_keepalive", hidden=True)
    def internal_keepalive() -> None:
        manager = create_runtime_manager(spawn_keepalive=False)
        while True:
            status = asyncio.run(manager.keepalive_once())
            if not status.connected:
                break
            time.sleep(60)
