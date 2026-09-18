"""Opt-in external routing exchange; the board primitives remain manual."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from .. import _meta
from .._app import mcp
from .session import _ERRORS, _board, _fail


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def export_routing_design(path: str, output_file: str) -> dict[str, Any]:
    """Export the current board as a Specctra DSN for an external router.

    This reads the open, possibly unsaved board. It does not alter its copper.
    A closed Edge.Cuts outline is required by KiCad's exporter.
    """
    try:
        written = _board(path).export_routing_design(output_file)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "dsn_path": str(written)}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def run_freerouting(
    dsn_path: str,
    ses_path: str,
    jar_path: str,
    java_path: str = "java",
    max_passes: int | None = None,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """Run a caller-selected FreeRouting JAR on a DSN and produce a SES.

    The JAR and Java runtime are installed by the caller. No board is edited.
    FreeRouting chooses the tracks; import and check its SES separately.
    """
    try:
        design = Path(dsn_path).resolve()
        session = Path(ses_path).resolve()
        jar = Path(jar_path).resolve()
        if design.suffix.lower() != ".dsn" or not design.is_file():
            raise ValueError("dsn_path must be an existing .dsn file")
        if session.suffix.lower() != ".ses" or session == design:
            raise ValueError("ses_path must be a different .ses file")
        if session.exists():
            raise ValueError(f"routing session already exists: {session}")
        if jar.suffix.lower() != ".jar" or not jar.is_file():
            raise ValueError("jar_path must be an existing FreeRouting .jar file")
        if max_passes is not None and max_passes < 1:
            raise ValueError("max_passes must be positive")
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        java = shutil.which(java_path)
        if java is None:
            raise ValueError("Java not found; install Java or supply java_path")
        session.parent.mkdir(parents=True, exist_ok=True)
        args = [java, "-jar", str(jar), "-de", str(design), "-do", str(session),
                "--gui.enabled=false"]
        if max_passes is not None:
            args.extend(["-mp", str(max_passes)])
        proc = subprocess.run(
            args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=timeout_seconds, check=False,
        )
        if proc.returncode != 0 or not session.is_file() or not session.stat().st_size:
            session.unlink(missing_ok=True)
            raise RuntimeError(
                f"FreeRouting failed (exit {proc.returncode}): "
                f"{(proc.stderr or proc.stdout)[-2000:]}"
            )
    except subprocess.TimeoutExpired as exc:
        session.unlink(missing_ok=True)
        return _fail(exc)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "ses_path": str(session), "log_tail": proc.stdout[-2000:]}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def import_routing_session(
    path: str, session_file: str, output_file: str
) -> dict[str, Any]:
    """Import a SES into a new board file and report its electrical state.

    The source board is never replaced. Use `check_board` and inspect copper
    on the returned path before choosing it as the working board.
    """
    try:
        written = _board(path).import_routing_session(session_file, output_file)
        routed = _board(str(written))
        found = routed.check()
        unrouted = routed.unrouted()
    except _ERRORS as exc:
        return _fail(exc)
    return {
        "ok": True,
        "board_path": str(written),
        "tracks": len(routed.tracks()),
        "vias": len(routed.vias()),
        "unrouted_count": len(unrouted),
        "drc_errors": sum(item.severity == "error" for item in found),
        "drc_warnings": sum(item.severity == "warning" for item in found),
    }
