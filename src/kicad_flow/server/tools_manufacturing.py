"""Provider requirements and independently composable manufacturing exports."""

from __future__ import annotations

import csv
import hashlib
import math
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from ..providers import fabrication_provider
from . import _meta
from ._app import mcp
from ._fabrication import profile_findings, read_profile
from .board_tools.session import _ERRORS, _board, _fail
from .schematic_tools.session import _sheet


def _requirements(path: str, service: str, sides: list[str]) -> dict[str, Any]:
    profile = read_profile(path)
    if profile is None:
        raise ValueError("set_fabrication_profile before manufacturing export")
    board = _board(path)
    findings = profile_findings(board, profile)
    if findings:
        raise ValueError(findings[0].message)
    return fabrication_provider(str(profile["provider"])).manufacturing_requirements(
        tuple(board.layers), service, tuple(sides),
    )


def _files(paths: list[Path]) -> list[dict[str, Any]]:
    return [{"path": str(p.resolve()), "size_bytes": p.stat().st_size,
             "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]


def _csv_write(output: str, columns: list[str], rows: list[dict[str, str]]) -> Path:
    target = Path(output).resolve()
    if target.suffix.lower() != ".csv" or target.exists():
        raise ValueError("output_file must be a new .csv file")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as name:
        scratch = Path(name) / target.name
        with scratch.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        os.rename(scratch, target)
    return target


def _positions(path: str, sides: list[str]) -> list[dict[str, str]]:
    return [r for r in _board(path).placement_rows() if r["side"] in sides]


def _formatted(rows: list[dict[str, str]], fields: list[str],
               columns: list[str], side_names: dict[str, str]) -> list[dict[str, str]]:
    return [dict(zip(columns, [side_names[r[f]] if f == "side" else r[f]
                               for f in fields], strict=True)) for r in rows]


def _bom(path: str, schematic_path: str, sides: list[str],
         field: str) -> list[dict[str, str]]:
    # Only omit references factually on the explicitly unselected board side.
    other = {r["ref"] for r in _positions(
        path, [s for s in ("front", "back") if s not in sides],
    )}
    return [r for r in _sheet(schematic_path).bom_rows(field)
            if r["ref"] not in other]


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def get_manufacturing_requirements(
    path: str, service: str, assembly_sides: list[str] | None = None,
) -> dict[str, Any]:
    """Read sourced required/recommended outputs from the active provider.

    Choose pcb or pcba. For pcba explicitly select front, back or both sides.
    Copper layers come from the board; no assembly side is inferred.
    """
    try:
        return {"ok": True, "requirements": _requirements(
            path, service, assembly_sides or [],
        )}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def export_gerbers(
    path: str, output_dir: str, service: str,
    assembly_sides: list[str] | None = None,
) -> dict[str, Any]:
    """Export provider-required layers into a new directory; return files/hashes.

    Uses native plotting with zone checks on a snapshot. Does not certify DRC.
    """
    try:
        requirements = _requirements(path, service, assembly_sides or [])
        files = _board(path).fabrication_files(
            output_dir, "gerbers", tuple(requirements["gerber"]["layers"]),
        )
        return {"ok": True, "files": _files(files), "requirements": requirements}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def export_drills(
    path: str, output_dir: str, include_map: bool = True,
) -> dict[str, Any]:
    """Export separate plated/non-plated drills and optional maps to a new folder."""
    try:
        _requirements(path, "pcb", [])
        files = _board(path).fabrication_files(
            output_dir, "drills", include_map=include_map,
        )
        return {"ok": True, "files": _files(files)}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def export_bom(
    path: str, schematic_path: str, output_file: str, assembly_sides: list[str],
    part_number_field: str = "LCSC",
) -> dict[str, Any]:
    """Export native schematic BOM fields using provider columns and chosen sides.

    No part selection or grouping is inferred. Missing supplier IDs remain blank.
    Save child sheets first when autosave is disabled; hierarchy reads disk.
    """
    try:
        requirements = _requirements(path, "pcba", assembly_sides)
        rows = _bom(path, schematic_path, assembly_sides, part_number_field)
        formatted = _formatted(rows, requirements["bom_fields"],
                               requirements["bom_columns"], {})
        output = _csv_write(output_file, requirements["bom_columns"], formatted)
        return {"ok": True, "files": _files([output]), "count": len(rows),
                "missing_part_numbers": [r["ref"] for r in rows
                                         if not r["part_number"].strip()]}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def export_placements(
    path: str, output_file: str, assembly_sides: list[str],
) -> dict[str, Any]:
    """Export native assembly origins/rotations in mm with provider CPL headers.

    DNP and position-file exclusions are respected. Supplier rotation corrections
    are not inferred. Review part orientations in the supplier assembly preview.
    """
    try:
        requirements = _requirements(path, "pcba", assembly_sides)
        rows = _positions(path, assembly_sides)
        formatted = _formatted(rows, requirements["placement_fields"],
                               requirements["cpl_columns"],
                               requirements["placement_side_names"])
        output = _csv_write(output_file, requirements["cpl_columns"], formatted)
        return {"ok": True, "files": _files([output]), "count": len(rows)}
    except _ERRORS as exc:
        return _fail(exc)


def _read_csv(path: str, columns: list[str], fields: list[str],
              side_names: dict[str, str]) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != columns:
            raise ValueError(f"{path}: expected columns {columns}")
        rows = list(reader)
    if any(None in r or any(v is None for v in r.values()) for r in rows):
        raise ValueError(f"{path}: malformed CSV row")
    reverse_sides = {v: k for k, v in side_names.items()}
    rows = [dict(zip(fields, [r[c] for c in columns], strict=True)) for r in rows]
    for row in rows:
        if "side" in row:
            if row["side"] not in reverse_sides:
                raise ValueError(f"{path}: invalid assembly side")
            row["side"] = reverse_sides[row["side"]]
    refs = [r["ref"].upper() for r in rows]
    if len(set(refs)) != len(refs) or any(not r for r in refs):
        raise ValueError(f"{path}: empty or duplicate designators")
    return rows


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def check_manufacturing_package(
    path: str, service: str, files: list[str],
    assembly_sides: list[str] | None = None, schematic_path: str = "",
    bom_file: str = "", placements_file: str = "", part_number_field: str = "LCSC",
) -> dict[str, Any]:
    """Check required outputs against fresh native exports and current design.

    Byte comparison detects stale/foreign CAM files. Native DRC and CSV readback
    check design findings and reference consistency. This is not a CAM geometry
    viewer or supplier assembly/impedance certification.
    """
    try:
        sides = assembly_sides or []
        requirements = _requirements(path, service, sides)
        board = _board(path)
        supplied = [Path(f).resolve() for f in files]
        if len({p.name.casefold() for p in supplied}) != len(supplied):
            raise ValueError("duplicate filenames in manufacturing files")
        if any(not p.is_file() or not p.stat().st_size for p in supplied):
            raise ValueError("missing or empty manufacturing file")
        issues: list[dict[str, str]] = []
        for kind in ("gerbers", "drills"):
            issues.extend({"code": code, "file": name} for code, name in
                          board.compare_fabrication_files(
                              tuple(supplied), kind,
                              tuple(requirements["gerber"]["layers"]),
                          ))
        if service == "pcba":
            if not schematic_path or not bom_file or not placements_file:
                raise ValueError("pcba check requires schematic_path, bom_file, "
                                 "and placements_file")
            if any(Path(f).resolve() not in supplied
                   for f in (bom_file, placements_file)):
                raise ValueError("BOM and CPL must be included in files")
            bom = _read_csv(bom_file, requirements["bom_columns"],
                            requirements["bom_fields"], {})
            positions = _read_csv(placements_file, requirements["cpl_columns"],
                                  requirements["placement_fields"],
                                  requirements["placement_side_names"])
            expected_bom = _bom(path, schematic_path, sides, part_number_field)
            if sorted(bom, key=str) != sorted(expected_bom, key=str):
                issues.append({"code": "stale_bom", "file": bom_file})
            native_positions = _positions(path, sides)
            if sorted(positions, key=str) != sorted(native_positions, key=str):
                issues.append({"code": "stale_placements", "file": placements_file})
            br = {r["ref"] for r in bom}
            pr = {r["ref"] for r in positions}
            for ref in sorted(br ^ pr):
                issues.append({"code": "bom_cpl_reference_mismatch", "ref": ref})
            for row in bom:
                if not row["part_number"].strip():
                    issues.append({"code": "missing_part_number",
                                   "ref": row["ref"]})
            for row in positions:
                if any(not math.isfinite(float(row[k]))
                       for k in ("x", "y", "rotation")):
                    issues.append({"code": "invalid_position",
                                   "ref": row["ref"]})
        drc = [f.as_dict() for f in board.check_proposed()]
        return {"ok": True, "passed": not issues and not drc,
                "issues": issues, "drc": drc, "files": _files(supplied),
                "requirements": requirements,
                "limitations": ["Supplier orientation and assembly review required.",
                                "No independent CAM rendering or impedance analysis."]}
    except (*_ERRORS, UnicodeError, csv.Error) as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def archive_manufacturing_files(files: list[str], output_zip: str) -> dict[str, Any]:
    """Archive explicit files under flat names; does not certify their contents."""
    try:
        paths = [Path(p).resolve() for p in files]
        target = Path(output_zip).resolve()
        if not paths or target.exists() or target.suffix.lower() != ".zip":
            raise ValueError("provide files and a new .zip destination")
        if len({p.name.casefold() for p in paths}) != len(paths):
            raise ValueError("duplicate archive filenames")
        if any(not p.is_file() or not p.stat().st_size for p in paths):
            raise ValueError("missing or empty archive input")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target.parent) as name:
            scratch = Path(name) / target.name
            with zipfile.ZipFile(scratch, "w", zipfile.ZIP_DEFLATED) as archive:
                for p in paths:
                    archive.write(p, p.name)
            os.rename(scratch, target)
        return {"ok": True, "files": _files([target]), "count": len(paths)}
    except _ERRORS as exc:
        return _fail(exc)
