from __future__ import annotations

from pathlib import Path

import pytest

from colab_cli.api.notebook import Notebook, NotebookHandle
from colab_cli.errors import ConnectionError


def test_offline_handle_edits(tmp_path: Path) -> None:
    nb = Notebook(tmp_path / "x.ipynb")

    assert nb.add("a = 1") == 0
    nb.add("print(a)")
    assert nb.state().cell_count == 2

    nb.edit(0, "a = 2")
    nb.move(0, 1)
    nb.delete(1)
    assert nb.state().cell_count == 1


def test_run_without_session_raises(tmp_path: Path) -> None:
    nb = Notebook(tmp_path / "x.ipynb")
    nb.add("x")

    with pytest.raises(ConnectionError):
        nb.run(0)


def test_run_dispatches_to_session_manager(tmp_path: Path) -> None:
    from colab_cli.api._sync import SyncRunner
    from colab_cli.models import CellResult

    path = tmp_path / "x.ipynb"

    class FakeManager:
        def __init__(self) -> None:
            self.calls: list[int] = []

        async def execute_cell(self, p, index, **kwargs):
            self.calls.append(index)
            return CellResult(index=index, source="x", status="success", stdout="ok\n")

    class FakeSession:
        def __init__(self) -> None:
            self.manager = FakeManager()
            # Use a real SyncRunner (private loop on a daemon thread) so this
            # test does not close the default event loop other tests rely on.
            self._runner = SyncRunner()

    session = FakeSession()
    handle = NotebookHandle(path, session=session, runner=session._runner)
    Notebook(path).add("print(1)")  # create the file via an offline handle

    result = handle.run(0)
    assert result.status == "success"
    assert session.manager.calls == [0]
