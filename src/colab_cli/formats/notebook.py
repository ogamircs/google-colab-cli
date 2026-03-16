"""Notebook parsing helpers."""

from __future__ import annotations

import json
from pathlib import Path


def extract_code_cells(path: Path) -> list[str]:
    notebook = json.loads(path.read_text())
    cells: list[str] = []
    for cell in notebook.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", [])
        if isinstance(source, list):
            cells.append("".join(source))
        elif isinstance(source, str):
            cells.append(source)
    return cells

