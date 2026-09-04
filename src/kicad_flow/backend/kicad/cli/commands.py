"""``kicad-cli``, as one class with a method per capability.

Every command this repo can issue is a method here, and assembling an argv is
nobody else's business. That is the whole point: a flag misspelled in a list
fails at runtime if you are lucky and produces a quietly different export if
you are not, and neither shows up until something downstream looks wrong.

The class covers the tool rather than our current appetite for it. `sch export
bom` and `pcb export pos` have no caller today; they are here because the CLI
has them, and a method that exists is one nobody writes an argv for later.

**It invokes; it does not interpret.** :meth:`erc` and :meth:`drc` hand back
KiCad's report as parsed JSON, not a verdict -- mapping a violation's position
to the part and pin that sits there is :meth:`Sheet.check`'s job, and turning a
DRC report into references is ``checks/drc.py``'s. Anything returning a file
returns its path.

There is no schematic/board split. The tool does not have one worth copying,
and a caller who wants a netlist does not care which half of KiCad owns it.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import _run

#: Formats ``pcb export`` will produce a 3D model in.
MODEL_FORMATS = ("step", "stl", "glb", "brep", "ply", "u3d", "vrml", "xao",
                 "stpz", "3dpdf")


class KiCadCLI:
    """The installed ``kicad-cli``, one method per command.

    Stateless apart from which executable to use, so the module-level
    :data:`cli` is fine for everything; construct your own only to point at a
    different KiCad.
    """

    def __init__(self, executable: str | None = None) -> None:
        """Use *executable*, or find one on PATH / beside the KiCad install."""
        self._exe = executable

    # -- availability -----------------------------------------------------


    def path(self) -> str | None:
        """Where the executable is, or None.

        Needed to locate KiCad's own Python and its symbol libraries, which
        sit beside it.
        """
        return self._exe or _run.find_kicad_cli()

    def require_path(self) -> str:
        """Where the executable is, or a :class:`KiCadCliError`.

        For callers that need the binary itself rather than a command --
        KiCad's bundled Python sits beside it, and that is how the board side
        reaches ``pcbnew``.
        """
        return self._exe or _run.require_kicad_cli()


    # -- schematic --------------------------------------------------------

    def erc(self, schematic: str | Path, *, severity_all: bool = True,
            units: str = "mm") -> dict[str, Any]:
        """Run the Electrical Rules Check; return KiCad's report as JSON.

        No ``--exit-code-violations``: a clean parse exits 0 even with
        violations, so only a genuinely bad file raises here.
        """
        args = ["sch", "erc", "--format", "json", "--units", units]
        if severity_all:
            args.append("--severity-all")
        return self._json(args, schematic, "erc.json")

    def sch_netlist(self, schematic: str | Path,
                    output_file: str | Path) -> Path:
        """Export a netlist -- what the sheet ACTUALLY connects."""
        return self._to_file(["sch", "export", "netlist"], schematic,
                             output_file)

    def sch_pdf(self, schematic: str | Path, output_file: str | Path, *,
                black_and_white: bool = False,
                exclude_drawing_sheet: bool = False,
                pages: str | None = None) -> Path:
        """Export the schematic to one PDF.

        *pages* is a comma-separated list of page numbers. There is no range
        syntax -- kicad-cli answers ``"2-4"`` with "No sheets to plot", which
        reads like an empty design rather than a rejected argument.
        """
        args = ["sch", "export", "pdf"]
        if black_and_white:
            args.append("--black-and-white")
        if exclude_drawing_sheet:
            args.append("--exclude-drawing-sheet")
        if pages:
            args += ["--pages", pages]
        return self._to_file(args, schematic, output_file)


    # -- board ------------------------------------------------------------

    def drc(self, board: str | Path, *, severity_all: bool = True,
            refill: bool = False, schematic_parity: bool = False,
            units: str = "mm") -> dict[str, Any]:
        """Run the Design Rules Check; return KiCad's report as JSON.

        *refill* refills zones for the check only. Without ``--save-board``
        that leaves the file alone: measure against the truth without
        mutating it.
        """
        args = ["pcb", "drc", "--format", "json", "--units", units]
        if severity_all:
            args.append("--severity-all")
        if refill:
            args.append("--refill-zones")
        if schematic_parity:
            args.append("--schematic-parity")
        return self._json(args, board, "drc.json")


    def pcb_render(self, board: str | Path, output_file: str | Path, *,
                   side: str = "top", width: int = 1200, height: int = 1200,
                   quality: str = "basic", rotate: str | None = None,
                   background: str = "opaque", zoom: float = 1.0) -> Path:
        """Render the board in 3D to a PNG or JPEG."""
        args = ["pcb", "render", "--side", side,
                "-w", str(int(width)), "-h", str(int(height)),
                "--quality", quality, "--background", background,
                "--zoom", str(float(zoom))]
        if rotate:
            args += ["--rotate", rotate]
        return self._to_file(args, board, output_file, timeout=600.0)


    # -- libraries --------------------------------------------------------


    # -- plumbing ---------------------------------------------------------

    def _run(self, args: list[str], *,
             timeout: float = 180.0) -> subprocess.CompletedProcess[str]:
        """Issue one command. The single place a subprocess is started."""
        return _run.run(args, timeout=timeout, executable=self._exe)

    def _to_file(self, args: list[str], source: str | Path,
                 output_file: str | Path, *, timeout: float = 180.0) -> Path:
        """Run *args* over *source* writing to *output_file*; return it."""
        out = Path(output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        self._run([*args, "-o", str(out), str(_require(source, "file"))],
                  timeout=timeout)
        return out


    def _json(self, args: list[str], source: str | Path,
              name: str) -> dict[str, Any]:
        """Run *args* writing JSON to a temp file; return it parsed."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / name
            self._run([*args, "-o", str(out), str(_require(source, "file"))])
            loaded: dict[str, Any] = json.loads(
                out.read_text(encoding="utf-8", errors="replace")
            )
            return loaded


def _require(path: str | Path, what: str) -> Path:
    """*path* as a Path, or a :class:`FileNotFoundError` naming *what*."""
    found = Path(path)
    if not found.is_file():
        raise FileNotFoundError(f"{what} not found: {found}")
    return found


#: The installed kicad-cli. Stateless, so one instance serves everything.
cli = KiCadCLI()

__all__ = ["MODEL_FORMATS", "KiCadCLI", "cli"]
