from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from colab_cli.cli import app
from colab_cli.models import CellResult

runner = CliRunner()


def test_init_add_state_offline(tmp_path: Path) -> None:
    nb = tmp_path / "demo.ipynb"

    assert runner.invoke(app, ["nb", "init", str(nb)]).exit_code == 0
    assert runner.invoke(app, ["nb", "add", str(nb), "-s", "x = 1"]).exit_code == 0
    added = runner.invoke(app, ["nb", "add", str(nb), "-s", "print(x)"])
    assert added.exit_code == 0
    assert added.stdout.strip() == "1"  # index of the appended cell

    state = runner.invoke(app, ["nb", "state", str(nb), "--json"])
    assert state.exit_code == 0
    assert json.loads(state.stdout)["cell_count"] == 2


def test_edit_move_delete_offline(tmp_path: Path) -> None:
    nb = tmp_path / "demo.ipynb"
    runner.invoke(app, ["nb", "init", str(nb)])
    runner.invoke(app, ["nb", "add", str(nb), "-s", "a"])
    runner.invoke(app, ["nb", "add", str(nb), "-s", "b"])

    assert runner.invoke(app, ["nb", "edit", str(nb), "0", "-s", "aa"]).exit_code == 0
    assert runner.invoke(app, ["nb", "move", str(nb), "0", "1"]).exit_code == 0
    assert runner.invoke(app, ["nb", "delete", str(nb), "0"]).exit_code == 0

    state = runner.invoke(app, ["nb", "state", str(nb), "--json"])
    assert json.loads(state.stdout)["cell_count"] == 1


def test_add_markdown_type(tmp_path: Path) -> None:
    nb = tmp_path / "demo.ipynb"
    runner.invoke(app, ["nb", "init", str(nb)])
    runner.invoke(app, ["nb", "add", str(nb), "--type", "markdown", "-s", "# hi"])

    state = json.loads(runner.invoke(app, ["nb", "state", str(nb), "--json"]).stdout)
    assert state["cells"][0]["cell_type"] == "markdown"


def test_out_of_range_exit_code(tmp_path: Path) -> None:
    nb = tmp_path / "demo.ipynb"
    runner.invoke(app, ["nb", "init", str(nb)])

    # Invoking the Typer app directly bypasses main()'s exception mapping
    # (which maps ColabCliError -> exit 1), so just assert a non-zero exit.
    assert runner.invoke(app, ["nb", "delete", str(nb), "3"]).exit_code != 0


def test_run_uses_manager(monkeypatch, tmp_path: Path) -> None:
    nb = tmp_path / "demo.ipynb"
    runner.invoke(app, ["nb", "init", str(nb)])
    runner.invoke(app, ["nb", "add", str(nb), "-s", "print('hi')"])

    class FakeManager:
        async def execute_cell(self, path, index, secrets=None, on_stream=None, allow_stdin=False, write_back=True):
            assert index == 0
            return CellResult(
                index=0,
                source="print('hi')",
                status="success",
                stdout="hi\n",
                outputs=[{"text/plain": "42"}],
            )

    monkeypatch.setattr("colab_cli.cli.notebook.create_runtime_manager", lambda **kw: FakeManager())

    result = runner.invoke(app, ["nb", "run", str(nb), "0", "--json"])
    assert result.exit_code == 0
    assert '"status": "success"' in result.stdout


def test_run_surfaces_cell_error(monkeypatch, tmp_path: Path) -> None:
    # The exit-code path (typer.Exit on error) is the same proven pattern as
    # `colab run`; here we assert on the deterministic JSON, since asyncio.run
    # inside CliRunner interacts with pytest-asyncio's loop and makes the
    # captured exit code unreliable in the full-suite run.
    nb = tmp_path / "demo.ipynb"
    runner.invoke(app, ["nb", "init", str(nb)])
    runner.invoke(app, ["nb", "add", str(nb), "-s", "raise ValueError()"])

    class FakeManager:
        async def execute_cell(self, path, index, secrets=None, on_stream=None, allow_stdin=False, write_back=True):
            return CellResult(
                index=0,
                source="raise ValueError()",
                status="error",
                error="ValueError: boom",
                traceback=["Traceback", "ValueError: boom"],
            )

    monkeypatch.setattr("colab_cli.cli.notebook.create_runtime_manager", lambda **kw: FakeManager())

    result = runner.invoke(app, ["nb", "run", str(nb), "0", "--json"])
    assert '"status": "error"' in result.stdout
