"""Notebook cell-editing handle for the Python API.

Editing methods (state/add/edit/delete/move/output) are pure local ``.ipynb``
file operations and need no runtime. ``run`` executes a cell on a bound
:class:`ColabSession`'s persistent kernel and writes outputs back to the file.

Use :class:`Notebook` for offline editing, or ``session.notebook(path)`` to get
a handle that can also execute cells.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from colab_cli.core.notebook import NotebookDocument
from colab_cli.errors import ConnectionError
from colab_cli.models import CellResult, NotebookState

if TYPE_CHECKING:
    from ._sync import SyncRunner
    from .session import ColabSession


class NotebookHandle:
    """Synchronous handle to a local ``.ipynb`` for cell editing and execution."""

    def __init__(
        self,
        path: str | Path,
        *,
        session: "ColabSession | None" = None,
        runner: "SyncRunner | None" = None,
    ) -> None:
        self._path = Path(path)
        self._session = session
        self._runner = runner

    @property
    def path(self) -> Path:
        return self._path

    # ----------------------------------------------------------- local edits

    def state(self) -> NotebookState:
        return NotebookDocument.load(self._path).state()

    def add(self, source: str, *, cell_type: str = "code", index: int | None = None) -> int:
        doc = NotebookDocument.load_or_create(self._path)
        at = doc.create_cell(cell_type, source, index)
        doc.save()
        return at

    def edit(self, index: int, source: str) -> None:
        doc = NotebookDocument.load(self._path)
        doc.edit_cell(index, source)
        doc.save()

    def delete(self, index: int) -> None:
        doc = NotebookDocument.load(self._path)
        doc.delete_cell(index)
        doc.save()

    def move(self, from_index: int, to_index: int) -> None:
        doc = NotebookDocument.load(self._path)
        doc.move_cell(from_index, to_index)
        doc.save()

    def output(self, index: int) -> list[dict[str, Any]]:
        return NotebookDocument.load(self._path).get_cell_output(index)

    # -------------------------------------------------------------- execute

    def run(
        self,
        index: int,
        *,
        secrets: dict[str, str] | None = None,
        on_stream: Callable[[str, str], Any] | None = None,
        allow_stdin: bool = False,
        write_back: bool = True,
        timeout: float | None = None,
    ) -> CellResult:
        """Execute cell ``index`` on the bound session's kernel; write outputs back.

        Never raises on remote code errors — inspect ``result.status``. Raises
        ``ConnectionError`` if this handle is not bound to a session.
        """
        if self._session is None:
            raise ConnectionError(
                "This notebook is not bound to a Colab session. Create it via "
                "colab(...).notebook(path) to execute cells."
            )
        runner = self._runner or self._session._runner
        return runner.run(
            self._session.manager.execute_cell(
                self._path,
                index,
                secrets=secrets,
                on_stream=on_stream,
                allow_stdin=allow_stdin,
                write_back=write_back,
            ),
            timeout=timeout,
        )


def Notebook(path: str | Path) -> NotebookHandle:
    """Return a session-less :class:`NotebookHandle` for offline editing."""
    return NotebookHandle(path)
