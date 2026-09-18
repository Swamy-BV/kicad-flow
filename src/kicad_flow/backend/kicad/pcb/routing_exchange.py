"""KiCad's Specctra exchange boundary for optional external routing."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .._sexpr import dumps
from ._runner import run_pcbnew
from ._state import BoardState

_EXPORT = """
import json
import sys
import pcbnew

with open(sys.argv[1], encoding='utf-8') as source:
    job = json.load(source)
board = pcbnew.LoadBoard(job['board'])
success = pcbnew.ExportSpecctraDSN(board, job['dsn'])
print(json.dumps({'ok': bool(success)}))
"""

_IMPORT = """
import json
import sys
import pcbnew

with open(sys.argv[1], encoding='utf-8') as source:
    job = json.load(source)
board = pcbnew.LoadBoard(job['board'])
success = pcbnew.ImportSpecctraSES(board, job['ses'])
if success:
    save_board(board, job['output'])
print(json.dumps({'ok': bool(success)}))
"""


def _snapshot(board: BoardState, directory: Path) -> Path:
    """Write the current in-memory board with its same-project rules."""
    target = directory / board._path.name
    target.write_text(dumps(board._tree) + "\n", encoding="utf-8")
    for suffix in (".kicad_pro", ".kicad_dru"):
        source = board._path.with_suffix(suffix)
        if source.is_file():
            shutil.copyfile(source, target.with_suffix(suffix))
    return target


def export_design(board: BoardState, output_file: str | Path) -> Path:
    """Export DSN from a snapshot, leaving the open board and disk untouched."""
    target = Path(output_file).resolve()
    if target.suffix.lower() != ".dsn":
        raise ValueError("routing design output must end in .dsn")
    board.outline_polygon(inset=0, max_error=0.05)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=board._path.parent) as name:
        snapshot = _snapshot(board, Path(name))
        scratch = Path(name) / target.name
        result = run_pcbnew(
            _EXPORT, {"board": str(snapshot), "dsn": str(scratch)}, timeout=60,
        )
        if not result.get("ok") or not scratch.is_file():
            raise RuntimeError("KiCad could not export the routing design")
        os.replace(scratch, target)
    return target


def import_session(
    board: BoardState, session_file: str | Path, output_file: str | Path
) -> Path:
    """Import SES into a separate board; never rewrite the source board."""
    session = Path(session_file).resolve()
    target = Path(output_file).resolve()
    if session.suffix.lower() != ".ses" or not session.is_file():
        raise ValueError("routing session must be an existing .ses file")
    if target.suffix.lower() != ".kicad_pcb":
        raise ValueError("routing output must end in .kicad_pcb")
    if target == board._path.resolve():
        raise ValueError("routing output must differ from the source board")
    if target.parent != board._path.parent.resolve():
        raise ValueError("routing output must be beside the source project")
    if target.exists():
        raise ValueError(f"routing output already exists: {target}")
    companions = (".kicad_pro", ".kicad_dru", ".kicad_sch")
    for suffix in companions:
        source = board._path.with_suffix(suffix)
        destination = target.with_suffix(suffix)
        if (destination.exists() and (not source.is_file()
                or source.read_bytes() != destination.read_bytes())):
            raise ValueError(f"routing output companion already differs: {destination}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=board._path.parent) as name:
        snapshot = _snapshot(board, Path(name))
        scratch = Path(name) / "routing-result.kicad_pcb"
        result = run_pcbnew(
            _IMPORT,
            {"board": str(snapshot), "ses": str(session), "output": str(scratch)},
            timeout=120,
        )
        if not result.get("ok") or not scratch.is_file():
            raise RuntimeError("KiCad could not import the routing session")
        # Prove the candidate parses before publishing it as a board.
        from .board import load

        routed = load(scratch)
        source_parts = sorted(
            (part.ref, part.fp_id, part.value, part.at, part.rotation, part.side)
            for part in board.footprints()
        )
        routed_parts = sorted(
            (part.ref, part.fp_id, part.value, part.at, part.rotation, part.side)
            for part in routed.footprints()
        )
        if routed_parts != source_parts:
            raise RuntimeError("routing import changed the footprint placement")
        source_pads = sorted(
            (part.ref, part.fp_id, pad.number, pad.net)
            for part in board.footprints() for pad in part.pads
        )
        routed_pads = sorted(
            (part.ref, part.fp_id, pad.number, pad.net)
            for part in routed.footprints() for pad in part.pads
        )
        if routed_pads != source_pads:
            raise RuntimeError("routing import changed footprint IDs or pad nets")
        if routed.layers != board.layers:
            raise RuntimeError("routing import changed the copper layer stack")
        if routed.graphics("Edge.Cuts") != board.graphics("Edge.Cuts"):
            raise RuntimeError("routing import changed the board outline")
        os.replace(scratch, target)
    for suffix in companions:
        source = board._path.with_suffix(suffix)
        destination = target.with_suffix(suffix)
        if source.is_file() and not destination.exists():
            shutil.copyfile(source, destination)
    return target
