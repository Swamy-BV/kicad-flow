"""Run scripts inside KiCad's bundled ``pcbnew`` Python.

``kicad-cli`` has no schematic->PCB command and cannot edit a board, so the PCB
side of kicad-flow drives KiCad's bundled ``pcbnew`` module by shelling out to
KiCad's own ``python.exe`` (found next to ``kicad-cli``). Every board operation
sends a small script plus a JSON *job*; the script prints one JSON line back.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .. import cli as kicad_cli

# Prepended to every script, after the print rebind. `save_board` lives here
# because the order of its three calls is the most expensive thing to get wrong
# in this project -- 407 DRC errors, 1044 DRC errors, four segfaults and a set
# of vias whose nets were permanently corrupted all came from doing them in a
# different order in a different script. Twenty-six scripts each decided it for
# themselves and they did not agree: `import_ses` landed tracks and never
# rebuilt connectivity, `set_layer_count` and `apply_process` changed the
# stackup and left every fill on the board stale.
_PRELUDE = """
def mm(v, places=3):
    '''Internal units to millimetres.'''
    return round(pcbnew.ToMM(v), places)


def at(x, y):
    '''Millimetres to a board point.'''
    return pcbnew.VECTOR2I(pcbnew.FromMM(float(x)), pcbnew.FromMM(float(y)))


def save_board(board, path, fill="refill"):
    '''Fill, rebuild connectivity, save -- in that order, always.

    Fill BEFORE connectivity. Connectivity is derived from what copper
    physically touches, so rebuilding it while a stale pour still overlaps the
    new copper merges their nets -- and the merge outlives a later refill: a
    via written as VBUS reads back as +3V3.

    *fill* is one of three, and the middle one is the one people forget:

    - "refill" recomputes the pours, which is right whenever copper moved.
    - "unfill" empties them, for when a refill is unsafe or premature --
      recomputing a fill against an outline being replaced in the same run
      takes pcbnew down, and a stackup change invalidates every layer. An
      empty pour is honest; a stale one is not, because everything downstream
      believes it.
    - "keep" leaves them exactly as they are, for a run that did not touch
      copper at all.
    '''
    zones = board.Zones()
    if len(zones) and fill != "keep":
        try:
            if fill == "refill":
                pcbnew.ZONE_FILLER(board).Fill(zones)
            else:
                for zone in zones:
                    zone.UnFill()
        except Exception:
            pass
    board.BuildConnectivity()
    pcbnew.SaveBoard(path, board)
"""


def kicad_python() -> str:
    """Return KiCad's bundled python.exe (sibling of kicad-cli)."""
    cli = Path(kicad_cli.cli.require_path())
    py = cli.parent / "python.exe"
    if not py.is_file():
        raise kicad_cli.KiCadCliError(f"KiCad python not found beside kicad-cli: {py}")
    return str(py)


def footprints_dir() -> Path:
    """Return KiCad's stock footprint directory (.../share/kicad/footprints)."""
    cli = Path(kicad_cli.cli.require_path())
    return cli.parent.parent / "share" / "kicad" / "footprints"


def run_pcbnew(
    script_src: str, job: dict[str, object], *, timeout: float = 120.0
) -> dict[str, Any]:
    """Run *script_src* in KiCad's python with *job* as JSON; return its output.

    Runs synchronously and returns only when pcbnew finishes (a small board
    takes ~1-2 s) -- there is no background run to wait for. A *timeout* guards
    against a stalled pcbnew so the call fails cleanly instead of blocking.

    The script receives the job JSON path as ``sys.argv[1]`` and reports by
    printing a single JSON object. That print does NOT go to stdout: a preamble
    rebinds ``print`` to write the result to a file instead. pcbnew's C++ side
    writes SWIG "memory leak" warnings straight to the stdout fd, unbuffered,
    while Python's print is buffered until exit -- so the two interleave and
    land spliced together mid-line. A complex board outline emits ~800 such
    warnings and reliably shredded the JSON. Scripts are unchanged; they still
    just print.

    Every script is also given :data:`_PRELUDE` -- ``mm``, ``at`` and
    ``save_board``, which each script used to define for itself.

    Raises:
        kicad_cli.KiCadCliError: If KiCad's python is missing, the run fails, or
            it exceeds *timeout* seconds.
    """
    with tempfile.TemporaryDirectory() as tmp:
        job_file = Path(tmp) / "job.json"
        script = Path(tmp) / "run.py"
        result_file = Path(tmp) / "result.json"
        job_file.write_text(json.dumps(job), encoding="utf-8")
        preamble = (
            f"_RESULT_PATH = {json.dumps(str(result_file))}\n"
            "def print(*args, **kwargs):\n"
            "    with open(_RESULT_PATH, 'w', encoding='utf-8') as _f:\n"
            "        _f.write(' '.join(str(a) for a in args))\n"
        )
        script.write_text(preamble + _PRELUDE + script_src, encoding="utf-8")
        try:
            # stdin=DEVNULL detaches the child from the parent's stdio. Launched
            # from an MCP stdio server the parent's stdin/stdout are the JSON-RPC
            # pipes; inheriting them lets the child (or a grandchild) hold the
            # pipe open so the read never sees EOF and run() blocks forever --
            # a hang that only appears under the server, never from a terminal.
            proc = subprocess.run(
                [kicad_python(), str(script), str(job_file)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise kicad_cli.KiCadCliError(
                f"pcbnew did not finish within {timeout:g}s -- it may be blocked "
                f"(is the board open in KiCad?). Nothing was returned."
            ) from e
        # Read it before leaving the block: `tmp` is deleted on the way out.
        payload = (
            result_file.read_text(encoding="utf-8")
            if result_file.is_file()
            else ""
        )
    if proc.returncode != 0:
        raise kicad_cli.KiCadCliError(
            f"pcbnew script failed (exit {proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}".strip()
        )
    if payload:
        try:
            written: dict[str, Any] = json.loads(payload)
            return written
        except json.JSONDecodeError:
            pass
    # Fall back to stdout for anything that writes there directly. pcbnew/wx can
    # emit warnings after our print, so the last line is not reliably ours;
    # taking it blindly crashed the caller with "Expecting value".
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                result: dict[str, Any] = json.loads(line)
                return result
            except json.JSONDecodeError:
                continue
    raise kicad_cli.KiCadCliError(
        "pcbnew produced no JSON result (exit 0). "
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}".strip()
    )
