"""Local notebook (.ipynb) document model for cell-level editing.

Pure-stdlib nbformat-v4 read/write (no ``nbformat`` dependency), mirroring the
plain-``json`` approach already used in ``formats/notebook.py``. Cell execution
lives in :meth:`RuntimeManager.execute_cell`, which routes a cell's source to the
runtime kernel and writes the results back via :meth:`NotebookDocument.write_outputs`.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from colab_cli.errors import ColabCliError
from colab_cli.models import CellResult, NotebookCellInfo, NotebookState

CODE = "code"
MARKDOWN = "markdown"
_VALID_TYPES = (CODE, MARKDOWN)


def _source_to_str(source: Any) -> str:
    if isinstance(source, list):
        return "".join(source)
    if isinstance(source, str):
        return source
    return ""


def cell_result_to_nb_outputs(result: CellResult) -> list[dict[str, Any]]:
    """Convert a :class:`CellResult` into nbformat-v4 cell ``outputs``.

    Fidelity note: the kernel accumulator collapses ``execute_result`` and
    ``display_data`` into bare MIME dicts (dropping ``execution_count``), so all
    rich outputs are written back as ``display_data``.
    """
    outputs: list[dict[str, Any]] = []
    if result.stdout:
        outputs.append({"output_type": "stream", "name": "stdout", "text": result.stdout})
    if result.stderr:
        outputs.append({"output_type": "stream", "name": "stderr", "text": result.stderr})
    for data in result.outputs:
        outputs.append({"output_type": "display_data", "data": data, "metadata": {}})
    if result.error:
        ename, _, evalue = result.error.partition(": ")
        outputs.append(
            {
                "output_type": "error",
                "ename": ename or "Error",
                "evalue": evalue,
                "traceback": list(result.traceback or []),
            }
        )
    return outputs


class NotebookDocument:
    """A local ``.ipynb`` notebook with addressable, editable cells."""

    def __init__(self, path: Path | str, data: dict[str, Any]) -> None:
        self.path = Path(path)
        self.data = data
        self.data.setdefault("cells", [])

    @classmethod
    def create_empty(cls, path: Path | str) -> "NotebookDocument":
        return cls(path, {"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5})

    @classmethod
    def load(cls, path: Path | str) -> "NotebookDocument":
        p = Path(path)
        if not p.exists():
            raise ColabCliError(f"Notebook not found: {p}")
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError as exc:
            raise ColabCliError(f"Invalid notebook JSON in {p}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("cells", []), list):
            raise ColabCliError(f"{p} is not a valid .ipynb notebook")
        return cls(p, data)

    @classmethod
    def load_or_create(cls, path: Path | str) -> "NotebookDocument":
        p = Path(path)
        return cls.load(p) if p.exists() else cls.create_empty(p)

    @property
    def cells(self) -> list[dict[str, Any]]:
        return self.data["cells"]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ reads

    def _check_index(self, index: int) -> None:
        if not isinstance(index, int) or index < 0 or index >= len(self.cells):
            raise ColabCliError(
                f"Cell index {index} out of range (notebook has {len(self.cells)} cell(s))."
            )

    def get_cell(self, index: int) -> dict[str, Any]:
        self._check_index(index)
        return self.cells[index]

    def source_of(self, index: int) -> str:
        return _source_to_str(self.get_cell(index).get("source"))

    def get_cell_output(self, index: int) -> list[dict[str, Any]]:
        return list(self.get_cell(index).get("outputs", []))

    def state(self) -> NotebookState:
        infos: list[NotebookCellInfo] = []
        for i, cell in enumerate(self.cells):
            cell_type = cell.get("cell_type", CODE)
            outputs = cell.get("outputs", []) if cell_type == CODE else []
            infos.append(
                NotebookCellInfo(
                    index=i,
                    id=cell.get("id"),
                    cell_type=cell_type,
                    source=_source_to_str(cell.get("source")),
                    execution_count=cell.get("execution_count"),
                    output_count=len(outputs),
                    has_error=any(o.get("output_type") == "error" for o in outputs),
                )
            )
        return NotebookState(path=str(self.path), cell_count=len(self.cells), cells=infos)

    # ----------------------------------------------------------------- writes

    def create_cell(self, cell_type: str, source: str, index: int | None = None) -> int:
        """Insert a new cell; append when ``index`` is ``None``. Returns its index."""
        if cell_type not in _VALID_TYPES:
            raise ColabCliError(f"Invalid cell type {cell_type!r}; expected 'code' or 'markdown'.")
        cell: dict[str, Any] = {
            "cell_type": cell_type,
            "id": uuid.uuid4().hex[:8],
            "metadata": {},
            "source": source,
        }
        if cell_type == CODE:
            cell["execution_count"] = None
            cell["outputs"] = []
        if index is None:
            self.cells.append(cell)
            return len(self.cells) - 1
        if index < 0 or index > len(self.cells):
            raise ColabCliError(f"Insert index {index} out of range (0..{len(self.cells)}).")
        self.cells.insert(index, cell)
        return index

    def edit_cell(self, index: int, source: str) -> None:
        """Replace a cell's source. Stale outputs/execution_count are cleared."""
        cell = self.get_cell(index)
        cell["source"] = source
        if cell.get("cell_type") == CODE:
            cell["execution_count"] = None
            cell["outputs"] = []

    def delete_cell(self, index: int) -> None:
        self._check_index(index)
        del self.cells[index]

    def move_cell(self, from_index: int, to_index: int) -> None:
        self._check_index(from_index)
        if to_index < 0 or to_index >= len(self.cells):
            raise ColabCliError(f"Target index {to_index} out of range (0..{len(self.cells) - 1}).")
        cell = self.cells.pop(from_index)
        self.cells.insert(to_index, cell)

    def write_outputs(self, index: int, result: CellResult) -> None:
        """Store execution results on a code cell and bump its execution_count."""
        cell = self.get_cell(index)
        if cell.get("cell_type") != CODE:
            raise ColabCliError(f"Cell {index} is not a code cell; cannot store outputs.")
        cell["outputs"] = cell_result_to_nb_outputs(result)
        counts = [c.get("execution_count") or 0 for c in self.cells if c.get("cell_type") == CODE]
        cell["execution_count"] = (max(counts) if counts else 0) + 1
