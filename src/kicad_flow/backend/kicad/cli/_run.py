"""Discovery of and a thin wrapper around the ``kicad-cli`` executable.

The server writes files itself, but export/validation delegates to KiCad's own
CLI. This module locates the binary (PATH first, then the default Windows
install location) and runs it, raising a clear error on failure.
"""

from __future__ import annotations

import shutil
import subprocess
from functools import cache
from pathlib import Path


class KiCadCliError(RuntimeError):
    """Raised when ``kicad-cli`` is missing or exits with a nonzero status."""


@cache
def find_kicad_cli() -> str | None:
    """Locate ``kicad-cli`` on PATH or at the default Windows install path.

    Cached: this sits under every symbol load, and the answer cannot change
    while the process runs (short of installing KiCad mid-session). Uncached it
    ran a PATH lookup plus a directory glob thousands of times per build. Call
    ``find_kicad_cli.cache_clear()`` if a test needs to re-detect.

    Returns:
        The absolute path to the executable, or ``None`` if it cannot be found.
        When several KiCad versions are installed, the highest is chosen.
    """
    found = shutil.which("kicad-cli")
    if found:
        return found
    for base in (r"C:\Program Files\KiCad", r"C:\Program Files (x86)\KiCad"):
        root = Path(base)
        if not root.exists():
            continue
        candidates = sorted(root.glob("*/bin/kicad-cli.exe"), reverse=True)
        if candidates:
            return str(candidates[0])
    return None


def require_kicad_cli() -> str:
    """Return the ``kicad-cli`` path or raise if it is not installed.

    Raises:
        KiCadCliError: If the executable cannot be located.
    """
    cli = find_kicad_cli()
    if cli is None:
        raise KiCadCliError(
            "kicad-cli not found. Install KiCad 10 or add kicad-cli to PATH."
        )
    return cli


def run(
    args: list[str], *, cwd: str | Path | None = None, timeout: float = 180.0,
    executable: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run ``kicad-cli`` with *args* and return the completed process.

    Args:
        args: Arguments following the executable, e.g.
            ``["sch", "export", "svg", "-o", "out", "board.kicad_sch"]``.
        cwd: Working directory for the subprocess.
        timeout: Seconds before the run is abandoned (guards a stalled cli).
        executable: A specific kicad-cli; the found one by default.

    Returns:
        The completed process, with ``stdout``/``stderr`` captured as text.

    Raises:
        KiCadCliError: If the executable is missing, exits nonzero, or stalls.
    """
    cli = executable or require_kicad_cli()
    # stdin=DEVNULL detaches the child from the parent's stdio; when launched
    # from an MCP stdio server, inheriting the JSON-RPC pipes can deadlock the
    # capture read so the call hangs (only under the server, never a terminal).
    try:
        proc = subprocess.run(
            [cli, *args],
            cwd=str(cwd) if cwd is not None else None,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise KiCadCliError(
            f"kicad-cli {' '.join(args[:2])} did not finish within {timeout:g}s"
        ) from e
    if proc.returncode != 0:
        raise KiCadCliError(
            f"kicad-cli {' '.join(args)} failed (exit {proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}".strip()
        )
    return proc
