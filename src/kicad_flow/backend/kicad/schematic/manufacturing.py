"""Schematic BOM export through KiCad's hierarchical exporter."""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from ..cli import cli


def bom_rows(source: Path, part_number_field: str) -> list[dict[str, str]]:
    """Read native fields, retaining one reference per row for validation."""
    with tempfile.TemporaryDirectory() as name:
        output = cli.bom(source, Path(name) / "bom.csv", part_number_field)
        with output.open(encoding="utf-8-sig", newline="") as stream:
            return [dict(row) for row in csv.DictReader(stream)]
