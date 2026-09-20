"""Native manufacturing exports from isolated board snapshots."""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path

from ..cli import cli
from ._state import BoardState
from .routing_exchange import _snapshot


def fabrication_files(
    board: BoardState, output_dir: str | Path, kind: str,
    layers: tuple[str, ...], include_map: bool,
) -> list[Path]:
    """Publish a new directory only after every native output succeeds."""
    target = Path(output_dir).resolve()
    if target.exists():
        raise ValueError("output_dir already exists; choose a fresh export directory")
    if kind == "gerbers" and not layers:
        raise ValueError("Gerber export requires explicit layers")
    board.outline_polygon(inset=0, max_error=0.05)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as name:
        root = Path(name)
        source = _snapshot(board, root)
        output = root / "output"
        output.mkdir()
        cli.fabrication(source, output, kind, layers, include_map)
        files = sorted(output.iterdir())
        if not files or any(not f.is_file() or not f.stat().st_size for f in files):
            raise RuntimeError("native export returned missing or empty output")
        os.rename(output, target)
        return [target / f.name for f in files]


def placement_rows(board: BoardState) -> list[dict[str, str]]:
    """Return native placement coordinates and rotations without correction guesses."""
    with tempfile.TemporaryDirectory(dir=board._path.parent) as name:
        root = Path(name)
        source = _snapshot(board, root)
        output = cli.positions(source, root / "placements.csv")
        with output.open(encoding="utf-8-sig", newline="") as stream:
            return [{"ref": row["Ref"], "x": row["PosX"], "y": row["PosY"],
                     "side": {"top": "front", "bottom": "back"}[row["Side"]],
                     "rotation": row["Rot"]} for row in csv.DictReader(stream)]


def compare_files(
    board: BoardState, files: tuple[Path, ...], kind: str, layers: tuple[str, ...],
) -> list[tuple[str, str]]:
    """Compare complete native records except timestamp-bearing comment lines."""
    indexed = {p.name: p for p in files}
    issues: list[tuple[str, str]] = []

    def content(file: Path) -> list[str]:
        return [line for line in file.read_text(encoding="utf-8").splitlines()
                if not line.startswith((
                    "G04 #@! TF.CreationDate,", "; #@! TF.CreationDate,",
                    "G04 Created by KiCad (", "; DRILL file KiCad ",
                ))]

    with tempfile.TemporaryDirectory() as name:
        expected = fabrication_files(
            board, Path(name) / kind, kind, layers, False,
        )
        for native in expected:
            if native.suffix == ".gbrjob":
                continue
            actual = indexed.get(native.name)
            if actual is None:
                issues.append(("missing_file", native.name))
            elif content(actual) != content(native):
                issues.append(("stale_or_different_file", native.name))
    return issues
