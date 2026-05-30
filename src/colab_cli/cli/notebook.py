"""Notebook cell-editing CLI commands (``colab nb ...``).

Editing commands (init/state/add/edit/delete/move/output) are pure local
``.ipynb`` operations and need no runtime or auth — they work offline. Only
``run`` connects to Colab to execute a cell on the persistent kernel.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import typer

from colab_cli.cli.run import _collect_secrets
from colab_cli.core.notebook import NotebookDocument
from colab_cli.core.runtime import create_runtime_manager
from colab_cli.formats.output import format_json


def _resolve_source(source: str | None, from_file: Path | None) -> str:
    if source is not None and from_file is not None:
        raise typer.BadParameter("Provide at most one of --source or --from-file.")
    if source is not None:
        return source
    if from_file is not None:
        return from_file.read_text()
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data:
            return data
    raise typer.BadParameter("No cell source provided — use --source, --from-file, or pipe stdin.")


def register(app: typer.Typer) -> None:
    nb = typer.Typer(
        no_args_is_help=True,
        help="Edit and execute notebook cells on a local .ipynb file.",
    )

    @nb.command("init")
    def init(
        path: Path = typer.Argument(..., resolve_path=True, help="New .ipynb path to create."),
    ) -> None:
        if path.exists():
            raise typer.BadParameter(f"{path} already exists.")
        doc = NotebookDocument.create_empty(path)
        doc.save()
        typer.echo(str(path))

    @nb.command("state")
    def state(
        path: Path = typer.Argument(..., exists=True, readable=True, resolve_path=True),
        as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
    ) -> None:
        st = NotebookDocument.load(path).state()
        if as_json:
            typer.echo(format_json(st))
            return
        typer.echo(f"{st.path}: {st.cell_count} cell(s)", err=True)
        for c in st.cells:
            head = c.source.splitlines()[0] if c.source else ""
            if len(head) > 60:
                head = head[:57] + "..."
            ec = c.execution_count if c.execution_count is not None else "-"
            flag = " ERR" if c.has_error else ""
            typer.echo(f"[{c.index}] {c.cell_type:<8} ec={ec} out={c.output_count}{flag}  {head}")

    @nb.command("add")
    def add(
        path: Path = typer.Argument(..., resolve_path=True),
        source: str | None = typer.Option(None, "--source", "-s", help="Cell source text."),
        from_file: Path | None = typer.Option(
            None, "--from-file", exists=True, readable=True, resolve_path=True, help="Read source from a file."
        ),
        cell_type: str = typer.Option("code", "--type", help="Cell type: code or markdown."),
        index: int | None = typer.Option(None, "--index", help="Insert position (default: append)."),
    ) -> None:
        src = _resolve_source(source, from_file)
        doc = NotebookDocument.load_or_create(path)
        at = doc.create_cell(cell_type, src, index)
        doc.save()
        typer.echo(str(at))

    @nb.command("edit")
    def edit(
        path: Path = typer.Argument(..., exists=True, readable=True, resolve_path=True),
        index: int = typer.Argument(...),
        source: str | None = typer.Option(None, "--source", "-s", help="New cell source text."),
        from_file: Path | None = typer.Option(
            None, "--from-file", exists=True, readable=True, resolve_path=True, help="Read source from a file."
        ),
    ) -> None:
        src = _resolve_source(source, from_file)
        doc = NotebookDocument.load(path)
        doc.edit_cell(index, src)
        doc.save()
        typer.echo(str(index))

    @nb.command("delete")
    def delete(
        path: Path = typer.Argument(..., exists=True, readable=True, resolve_path=True),
        index: int = typer.Argument(...),
    ) -> None:
        doc = NotebookDocument.load(path)
        doc.delete_cell(index)
        doc.save()
        typer.echo(f"deleted cell {index}", err=True)

    @nb.command("move")
    def move(
        path: Path = typer.Argument(..., exists=True, readable=True, resolve_path=True),
        from_index: int = typer.Argument(...),
        to_index: int = typer.Argument(...),
    ) -> None:
        doc = NotebookDocument.load(path)
        doc.move_cell(from_index, to_index)
        doc.save()
        typer.echo(f"moved cell {from_index} -> {to_index}", err=True)

    @nb.command("output")
    def output(
        path: Path = typer.Argument(..., exists=True, readable=True, resolve_path=True),
        index: int = typer.Argument(...),
        as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
    ) -> None:
        outputs = NotebookDocument.load(path).get_cell_output(index)
        if as_json:
            typer.echo(format_json({"index": index, "outputs": outputs}))
            return
        for o in outputs:
            otype = o.get("output_type")
            if otype == "stream":
                typer.echo(o.get("text", ""), nl=False, err=o.get("name") == "stderr")
            elif otype in {"display_data", "execute_result"}:
                text = (o.get("data") or {}).get("text/plain")
                if text:
                    typer.echo(text)
            elif otype == "error":
                typer.echo("\n".join(o.get("traceback", [])), err=True)

    @nb.command("run")
    def run(
        path: Path = typer.Argument(..., exists=True, readable=True, resolve_path=True),
        index: int = typer.Argument(...),
        secret: list[str] | None = typer.Option(None, "--secret", "-s", help="Secret as KEY=VALUE (repeatable)."),
        secrets_file: Path | None = typer.Option(
            None, "--secrets-file", exists=True, readable=True, resolve_path=True, help="Path to KEY=VALUE secrets file."
        ),
        as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
    ) -> None:
        secrets = _collect_secrets(secret, secrets_file)
        manager = create_runtime_manager(spawn_keepalive=False)
        if as_json:
            result = asyncio.run(manager.execute_cell(path, index, secrets=secrets))
            typer.echo(format_json(result))
            if result.status == "error":
                raise typer.Exit(code=1)
            return

        def on_stream(channel: str, text: str) -> None:
            typer.echo(text, nl=False, err=channel == "stderr")

        result = asyncio.run(manager.execute_cell(path, index, on_stream=on_stream, secrets=secrets))
        for o in result.outputs:
            text = o.get("text/plain")
            if text:
                typer.echo(text)
        if result.traceback:
            typer.echo("\n".join(result.traceback), err=True)
        if result.status == "error":
            raise typer.Exit(code=1)

    app.add_typer(nb, name="nb")
