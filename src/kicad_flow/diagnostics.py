"""Read-only installation diagnostics for source and packaged releases."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from importlib import metadata, resources
from pathlib import Path

from kicad_flow import __version__
from kicad_flow.backend.kicad.cli import KiCadCliError, cli
from kicad_flow.providers import fabrication_provider, parts_provider


@dataclass(frozen=True)
class Diagnostic:
    """One observable release prerequisite and its result."""

    name: str
    status: str
    detail: str


def _check_kicad() -> Diagnostic:
    """Report the KiCad CLI path and version."""
    path = cli.path()
    if path is None:
        return Diagnostic("KiCad CLI", "FAIL", "kicad-cli was not found")
    try:
        version = cli.version()
    except KiCadCliError as exc:
        return Diagnostic("KiCad CLI", "FAIL", str(exc))
    return Diagnostic("KiCad CLI", "PASS", f"{version} at {path}")


def _check_pymupdf() -> Diagnostic:
    """Report the installed PDF rasterizer version."""
    try:
        version = metadata.version("PyMuPDF")
    except metadata.PackageNotFoundError:
        return Diagnostic("PyMuPDF", "FAIL", "package metadata was not found")
    return Diagnostic("PyMuPDF", "PASS", version)


def _check_dashboard() -> Diagnostic:
    """Confirm that the packaged monitor assets are readable."""
    try:
        asset = (
            resources.files("kicad_flow.monitor")
            .joinpath("static")
            .joinpath("index.html")
        )
        size = len(asset.read_bytes())
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        return Diagnostic("Dashboard", "FAIL", str(exc))
    return Diagnostic("Dashboard", "PASS", f"index.html ({size} bytes)")


def _check_fabrication_data() -> Diagnostic:
    """Confirm that the small, bundled fabrication snapshot is usable."""
    try:
        capabilities = fabrication_provider("jlcpcb").capabilities()
    except (OSError, TypeError, ValueError) as exc:
        return Diagnostic("JLCPCB capabilities", "FAIL", str(exc))
    return Diagnostic(
        "JLCPCB capabilities",
        "PASS",
        f"layers {', '.join(str(value) for value in capabilities.layers)}",
    )


def _check_parts_database() -> Diagnostic:
    """Report the optional external parts database without changing it."""
    status = parts_provider("jlcpcb").status()
    if not status.available:
        detail = status.error or f"not found at {status.database_path}"
        return Diagnostic("JLCPCB parts database", "WARN", detail)
    count = (
        "unknown count"
        if status.part_count is None
        else f"{status.part_count:,} parts"
    )
    return Diagnostic(
        "JLCPCB parts database", "PASS", f"{count} at {status.database_path}"
    )


def _check_working_directory() -> Diagnostic:
    """Report whether the current directory is writable without writing to it."""
    directory = Path.cwd()
    if os.access(directory, os.W_OK):
        return Diagnostic("Working directory", "PASS", str(directory))
    return Diagnostic("Working directory", "FAIL", f"not writable: {directory}")


def run_diagnostics() -> list[Diagnostic]:
    """Run all non-mutating release checks."""
    mode = "packaged executable" if getattr(sys, "frozen", False) else "Python"
    return [
        Diagnostic("KiCadFlow", "PASS", f"{__version__} ({mode})"),
        _check_kicad(),
        _check_pymupdf(),
        _check_dashboard(),
        _check_fabrication_data(),
        _check_parts_database(),
        _check_working_directory(),
    ]


def format_diagnostics(checks: list[Diagnostic], *, as_json: bool) -> str:
    """Render diagnostics for a human or an automated release check."""
    if as_json:
        return json.dumps([asdict(check) for check in checks], indent=2)
    width = max(len(check.name) for check in checks)
    return "\n".join(
        f"{check.status:<4}  {check.name:<{width}}  {check.detail}" for check in checks
    )


__all__ = ["Diagnostic", "format_diagnostics", "run_diagnostics"]
