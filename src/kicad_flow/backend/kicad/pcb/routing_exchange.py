"""KiCad's Specctra exchange boundary for optional external routing."""

from __future__ import annotations

import math
import os
import shutil
import tempfile
from pathlib import Path

from kicad_flow.pcb.types import Point

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

_RESOLVED_OUTLINE = r"""
import json, sys
import pcbnew

job = json.load(open(sys.argv[1], encoding="utf-8"))
board = pcbnew.LoadBoard(job["board"])
board.GetDesignSettings().m_MaxError = pcbnew.FromMM(float(job["max_error"]))
shape = pcbnew.SHAPE_POLY_SET()
valid = board.GetBoardPolygonOutlines(shape, False, None, True, False)
if not valid:
    print(json.dumps({"ok": False, "error": "invalid closed Edge.Cuts outline"}))
else:
    contours = []
    def add(kind, parent, chain):
        contours.append({
            "kind": kind,
            "parent": parent,
            "points": [[mm(chain.CPoint(i).x, 6), mm(chain.CPoint(i).y, 6)]
                       for i in range(chain.PointCount())],
        })
    for outer in range(shape.OutlineCount()):
        add("outline", outer, shape.COutline(outer))
        for hole in range(shape.HoleCount(outer)):
            add("hole", outer, shape.CHole(outer, hole))
    print(json.dumps({"ok": True, "contours": contours}))
"""

_OUTLINE_MAX_ERROR_MM = 0.005
_OUTLINE_TOLERANCE_MM = 0.02


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
    companions = (".kicad_pro", ".kicad_dru", ".kicad_sch", ".kicad-flow.json")
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
        outline_difference = _outline_difference(snapshot, scratch)
        if outline_difference:
            raise RuntimeError(
                f"routing import changed the board outline: {outline_difference}"
            )
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
        os.replace(scratch, target)
    for suffix in companions:
        source = board._path.with_suffix(suffix)
        destination = target.with_suffix(suffix)
        if source.is_file() and not destination.exists():
            shutil.copyfile(source, destination)
    return target


def _resolved_contours(path: Path) -> list[tuple[str, tuple[Point, ...]]]:
    """Resolve every outside and cutout contour through KiCad itself."""
    result = run_pcbnew(
        _RESOLVED_OUTLINE,
        {"board": str(path), "max_error": _OUTLINE_MAX_ERROR_MM},
    )
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "invalid Edge.Cuts outline"))
    contours: list[tuple[str, tuple[Point, ...]]] = []
    for raw in result.get("contours", []):
        points = tuple(Point(float(x), float(y)) for x, y in raw["points"])
        contours.append((str(raw["kind"]), points))
    return contours


def _outline_difference(source: Path, routed: Path) -> str:
    """Describe a real geometric outline difference, ignoring representation."""
    before = _resolved_contours(source)
    after = _resolved_contours(routed)
    for kind in ("outline", "hole"):
        left = [points for name, points in before if name == kind]
        right = [points for name, points in after if name == kind]
        if len(left) != len(right):
            return f"{kind} contour count is {len(right)}, expected {len(left)}"
        remaining = list(right)
        for index, contour in enumerate(left):
            if not remaining:
                return f"missing {kind} contour {index}"
            choices = [(_ring_deviation(contour, candidate), offset)
                       for offset, candidate in enumerate(remaining)]
            deviation, match = min(choices)
            if deviation > _OUTLINE_TOLERANCE_MM:
                return (
                    f"{kind} contour {index} deviates by {deviation:.6f} mm; "
                    f"tolerance is {_OUTLINE_TOLERANCE_MM:.6f} mm"
                )
            remaining.pop(match)
    return ""


def _ring_deviation(first: tuple[Point, ...], second: tuple[Point, ...]) -> float:
    """Symmetric point-to-segment deviation for two closed sampled contours."""
    if len(first) < 3 or len(second) < 3:
        return math.inf
    return max(_directed_deviation(first, second), _directed_deviation(second, first))


def _directed_deviation(points: tuple[Point, ...], ring: tuple[Point, ...]) -> float:
    """Largest distance from sampled *points* to the closed polyline *ring*."""
    segments = list(zip(ring, (*ring[1:], ring[0]), strict=True))
    return max(
        min(_point_segment_distance(point, start, end) for start, end in segments)
        for point in points
    )


def _point_segment_distance(point: Point, start: Point, end: Point) -> float:
    """Euclidean distance from one point to a finite line segment."""
    dx, dy = end.x - start.x, end.y - start.y
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return math.hypot(point.x - start.x, point.y - start.y)
    position = ((point.x - start.x) * dx + (point.y - start.y) * dy) / length_squared
    position = max(0.0, min(1.0, position))
    x, y = start.x + position * dx, start.y + position * dy
    return math.hypot(point.x - x, point.y - y)
