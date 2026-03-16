"""Remote file operation CLI commands."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from colab_cli.core.runtime import create_runtime_manager
from colab_cli.formats.output import format_json


def register(app: typer.Typer) -> None:
    @app.command("push")
    def push(
        local_path: Path = typer.Argument(..., exists=True, readable=True, resolve_path=True),
        remote_path: str = typer.Argument(...),
    ) -> None:
        asyncio.run(create_runtime_manager(spawn_keepalive=False).push_file(local_path, remote_path))
        typer.echo(remote_path)

    @app.command("pull")
    def pull(
        remote_path: str = typer.Argument(...),
        local_path: Path = typer.Argument(..., resolve_path=True),
    ) -> None:
        path = asyncio.run(create_runtime_manager(spawn_keepalive=False).pull_file(remote_path, local_path))
        typer.echo(str(path))

    @app.command("ls")
    def ls(
        remote_path: str = typer.Argument("", help="Remote directory path."),
        as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
    ) -> None:
        items = asyncio.run(create_runtime_manager(spawn_keepalive=False).list_files(remote_path))
        if as_json:
            typer.echo(format_json({"items": [item.model_dump(mode="json") for item in items]}))
            return
        for item in items:
            typer.echo(item.path)

