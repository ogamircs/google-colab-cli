"""Remote execution CLI commands."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from colab_cli.core.runtime import create_runtime_manager
from colab_cli.core.secrets import parse_key_value, parse_secrets_file
from colab_cli.errors import ConfigError
from colab_cli.formats.output import format_json
from colab_cli.models import RunResult


def _collect_secrets(
    secret: list[str] | None,
    secrets_file: Path | None,
) -> dict[str, str]:
    """Merge secrets from file and CLI flags. CLI flags win on conflict."""
    merged: dict[str, str] = {}
    if secrets_file is not None:
        merged.update(parse_secrets_file(secrets_file))
    for item in secret or []:
        try:
            key, value = parse_key_value(item)
        except ConfigError:
            raise typer.BadParameter(f"Invalid secret format: {item!r} — expected KEY=VALUE")
        merged[key] = value
    return merged


def register(app: typer.Typer) -> None:
    @app.command("run")
    def run(
        target: Path | None = typer.Argument(None, exists=True, readable=True, resolve_path=True),
        code: str | None = typer.Option(None, "--code", "-c", help="Inline Python code to execute."),
        secret: list[str] | None = typer.Option(None, "--secret", "-s", help="Secret as KEY=VALUE (repeatable)."),
        secrets_file: Path | None = typer.Option(None, "--secrets-file", exists=True, readable=True, resolve_path=True, help="Path to file with KEY=VALUE secrets."),
        as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
    ) -> None:
        if bool(target) == bool(code):
            raise typer.BadParameter("Provide exactly one of a file path or --code.")

        secrets = _collect_secrets(secret, secrets_file)
        manager = create_runtime_manager(spawn_keepalive=False)
        if as_json:
            result = _run_command(manager, target, code, secrets=secrets)
            typer.echo(format_json(result))
            if result.exit_code:
                raise typer.Exit(code=result.exit_code)
            return

        def on_stream(channel: str, text: str) -> None:
            typer.echo(text, nl=False, err=channel == "stderr")

        result = _run_command(manager, target, code, on_stream=on_stream, secrets=secrets)
        _emit_non_stream_outputs(result)
        if result.traceback:
            typer.echo("\n".join(result.traceback), err=True)
        if result.exit_code:
            raise typer.Exit(code=result.exit_code)


def _run_command(
    manager,
    target: Path | None,
    code: str | None,
    on_stream=None,
    secrets: dict[str, str] | None = None,
) -> RunResult:
    if code is not None:
        return asyncio.run(manager.run_code(code, source_name="inline.py", on_stream=on_stream, secrets=secrets))
    assert target is not None
    if target.suffix == ".ipynb":
        return asyncio.run(
            manager.run_notebook(
                target,
                on_stream=on_stream,
                on_cell_start=lambda index, total: typer.echo(f"[{index}/{total}] running cell", err=True),
                secrets=secrets,
            )
        )
    return asyncio.run(manager.run_script(target, on_stream=on_stream, secrets=secrets))


def _emit_non_stream_outputs(result: RunResult) -> None:
    for cell in result.cells:
        for output in cell.outputs:
            text = output.get("text/plain")
            if text:
                typer.echo(text)

