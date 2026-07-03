"""Typer application entrypoint for colab-cli."""

from __future__ import annotations

import typer

from colab_cli.errors import AuthError, ColabCliError, ColabRuntimeError, ConnectionError, ExecutionError

from . import connect, files, notebook, run
from .auth import auth_app

app = typer.Typer(add_completion=False, no_args_is_help=True)

app.add_typer(auth_app, name="auth")
connect.register(app)
run.register(app)
files.register(app)
notebook.register(app)


def main() -> None:
    # typer.Exit only works inside app(); out here it would escape as an
    # unhandled exception (traceback + exit 1). Use SystemExit so the mapped
    # exit codes actually reach the shell.
    try:
        app()
    except ExecutionError as exc:
        typer.echo(str(exc), err=True)
        raise SystemExit(1) from exc
    except (AuthError, ConnectionError) as exc:
        typer.echo(str(exc), err=True)
        raise SystemExit(2) from exc
    except ColabRuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise SystemExit(3) from exc
    except ColabCliError as exc:
        typer.echo(str(exc), err=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
