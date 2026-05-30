from __future__ import annotations

import json
from pathlib import Path

import pytest

from colab_cli.core.notebook import NotebookDocument, cell_result_to_nb_outputs
from colab_cli.errors import ColabCliError
from colab_cli.models import CellResult


def test_create_empty_and_save_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "nb.ipynb"
    NotebookDocument.create_empty(p).save()

    data = json.loads(p.read_text())
    assert data["nbformat"] == 4
    assert data["cells"] == []


def test_create_edit_move_delete(tmp_path: Path) -> None:
    doc = NotebookDocument.create_empty(tmp_path / "nb.ipynb")

    assert doc.create_cell("code", "a = 1") == 0
    assert doc.create_cell("code", "b = 2") == 1
    assert doc.create_cell("markdown", "# title", index=0) == 0

    assert [c.cell_type for c in doc.state().cells] == ["markdown", "code", "code"]

    doc.edit_cell(1, "a = 99")
    assert doc.source_of(1) == "a = 99"

    doc.move_cell(0, 2)
    assert [c.cell_type for c in doc.state().cells] == ["code", "code", "markdown"]

    doc.delete_cell(2)
    assert doc.state().cell_count == 2


def test_index_and_type_errors(tmp_path: Path) -> None:
    doc = NotebookDocument.create_empty(tmp_path / "nb.ipynb")
    doc.create_cell("code", "x")

    with pytest.raises(ColabCliError):
        doc.edit_cell(5, "y")
    with pytest.raises(ColabCliError):
        doc.delete_cell(-1)
    with pytest.raises(ColabCliError):
        doc.move_cell(0, 9)
    with pytest.raises(ColabCliError):
        doc.create_cell("sql", "x")
    with pytest.raises(ColabCliError):
        doc.create_cell("code", "x", index=99)


def test_load_missing_and_invalid(tmp_path: Path) -> None:
    with pytest.raises(ColabCliError):
        NotebookDocument.load(tmp_path / "nope.ipynb")

    bad = tmp_path / "bad.ipynb"
    bad.write_text("{not json")
    with pytest.raises(ColabCliError):
        NotebookDocument.load(bad)


def test_write_outputs_and_get_output(tmp_path: Path) -> None:
    p = tmp_path / "nb.ipynb"
    doc = NotebookDocument.create_empty(p)
    doc.create_cell("code", "print('hi')")
    result = CellResult(
        index=0,
        source="print('hi')",
        status="success",
        stdout="hi\n",
        outputs=[{"text/plain": "42"}],
    )

    doc.write_outputs(0, result)
    doc.save()

    reloaded = NotebookDocument.load(p)
    outs = reloaded.get_cell_output(0)
    assert any(o["output_type"] == "stream" and o["text"] == "hi\n" for o in outs)
    assert any(o["output_type"] == "display_data" for o in outs)
    assert reloaded.state().cells[0].execution_count == 1


def test_cell_result_to_nb_outputs_error_mapping() -> None:
    result = CellResult(
        index=0,
        source="raise",
        status="error",
        error="ValueError: boom",
        traceback=["tb1", "tb2"],
    )

    err = next(o for o in cell_result_to_nb_outputs(result) if o["output_type"] == "error")
    assert err["ename"] == "ValueError"
    assert err["evalue"] == "boom"
    assert err["traceback"] == ["tb1", "tb2"]


def test_edit_clears_stale_outputs(tmp_path: Path) -> None:
    doc = NotebookDocument.create_empty(tmp_path / "nb.ipynb")
    doc.create_cell("code", "x")
    doc.write_outputs(0, CellResult(index=0, source="x", status="success", stdout="1\n"))

    doc.edit_cell(0, "y")

    assert doc.get_cell_output(0) == []
    assert doc.state().cells[0].execution_count is None


def test_state_reports_has_error(tmp_path: Path) -> None:
    doc = NotebookDocument.create_empty(tmp_path / "nb.ipynb")
    doc.create_cell("code", "boom")
    doc.write_outputs(
        0, CellResult(index=0, source="boom", status="error", error="E: x", traceback=["t"])
    )

    assert doc.state().cells[0].has_error is True
