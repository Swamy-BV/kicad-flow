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

    def available(self) -> bool:
        """Whether a ``kicad-cli`` can be found at all.

        The monitor asks this to decide whether to offer a render, rather
        than letting an export fail and catching it.
        """
        return (self._exe or _run.find_kicad_cli()) is not None

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

    def version(self) -> str:
        """The installed KiCad version, e.g. ``10.0.0``."""
        return self._run(["version"]).stdout.strip()

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

    def sch_svg(self, schematic: str | Path, output_dir: str | Path, *,
                black_and_white: bool = False,
                exclude_drawing_sheet: bool = False,
                no_background: bool = False,
                pages: str | None = None) -> Path:
        """Export the schematic to SVG, one file per page, into *output_dir*."""
        args = ["sch", "export", "svg"]
        if black_and_white:
            args.append("--black-and-white")
        if exclude_drawing_sheet:
            args.append("--exclude-drawing-sheet")
        if no_background:
            args.append("--no-background")
        if pages:
            args += ["--pages", pages]
        return self._to_dir(args, schematic, output_dir)

    def sch_bom(self, schematic: str | Path, output_file: str | Path, *,
                fields: str | None = None,
                group_by: str | None = None) -> Path:
        """Export a Bill of Materials CSV, straight from KiCad."""
        args = ["sch", "export", "bom"]
        if fields:
            args += ["--fields", fields]
        if group_by:
            args += ["--group-by", group_by]
        return self._to_file(args, schematic, output_file)

    def sch_plot(self, schematic: str | Path, output_file: str | Path, *,
                 fmt: str = "dxf") -> Path:
        """Export the schematic as ``dxf``, ``ps`` or ``hpgl``."""
        if fmt not in ("dxf", "ps", "hpgl"):
            raise ValueError(f"sch plot format must be dxf, ps or hpgl, not {fmt!r}")
        return self._to_file(["sch", "export", fmt], schematic, output_file)

    def sch_upgrade(self, schematic: str | Path) -> Path:
        """Migrate a schematic to the installed KiCad's file format."""
        path = _require(schematic, "schematic")
        self._run(["sch", "upgrade", str(path)])
        return path

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

    def pcb_stats(self, board: str | Path) -> dict[str, Any]:
        """Board statistics -- size, layers, pad and via counts -- as JSON."""
        return self._json(
            ["pcb", "export", "stats", "--format", "json", "--units", "mm",
             "--subtract-holes-from-board"], board, "stats.json")

    def pcb_pdf(self, board: str | Path, output_file: str | Path, layers: str,
                *, black_and_white: bool = False,
                include_border_title: bool = True,
                single_page: bool = True) -> Path:
        """Plot *layers* to a PDF."""
        args = ["pcb", "export", "pdf", "--layers", layers]
        if include_border_title:
            args.append("--include-border-title")
        if single_page:
            args.append("--mode-single")
        if black_and_white:
            args.append("--black-and-white")
        return self._to_file(args, board, output_file)

    def pcb_svg(self, board: str | Path, output_file: str | Path,
                layers: str, *, black_and_white: bool = False) -> Path:
        """Plot *layers* to SVG."""
        args = ["pcb", "export", "svg", "--layers", layers]
        if black_and_white:
            args.append("--black-and-white")
        return self._to_file(args, board, output_file)

    def pcb_gerbers(self, board: str | Path, output_dir: str | Path,
                    layers: list[str], *, check_zones: bool = False,
                    subtract_soldermask: bool = False,
                    protel_extensions: bool = True) -> Path:
        """Plot Gerbers for *layers* into *output_dir*."""
        args = ["pcb", "export", "gerbers", "-l", ",".join(layers)]
        if check_zones:
            args.append("--check-zones")
        if subtract_soldermask:
            args.append("--subtract-soldermask")
        if not protel_extensions:
            args.append("--no-protel-ext")
        return self._to_dir(args, board, output_dir)

    def pcb_drill(self, board: str | Path, output_dir: str | Path, *,
                  drill_map: bool = False, separate_th: bool = False) -> Path:
        """Generate drill files into *output_dir*.

        The trailing separator is not cosmetic: kicad-cli treats ``-o`` as a
        directory for drill output only when it ends in one.
        """
        args = ["pcb", "export", "drill"]
        if drill_map:
            args += ["--generate-map", "--map-format", "pdf"]
        if separate_th:
            args.append("--excellon-separate-th")
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        self._run([*args, "-o", str(out) + "/", str(_require(board, "board"))])
        return out

    def pcb_pos(self, board: str | Path, output_file: str | Path, *,
                fmt: str = "csv", units: str = "mm",
                side: str = "both", use_drill_origin: bool = False) -> Path:
        """Generate the pick-and-place position file."""
        args = ["pcb", "export", "pos", "--format", fmt, "--units", units,
                "--side", side]
        if use_drill_origin:
            args.append("--use-drill-file-origin")
        return self._to_file(args, board, output_file)

    def pcb_ipcd356(self, board: str | Path, output_file: str | Path) -> Path:
        """Write the IPC-D-356 bare-board electrical test netlist."""
        return self._to_file(["pcb", "export", "ipcd356"], board, output_file)

    def pcb_model(self, board: str | Path, output_file: str | Path, *,
                  fmt: str = "step", include_tracks: bool = False,
                  include_zones: bool = False,
                  subst_models: bool = True) -> Path:
        """Export a 3D model in one of :data:`MODEL_FORMATS`."""
        if fmt not in MODEL_FORMATS:
            raise ValueError(
                f"model format must be one of {list(MODEL_FORMATS)}, not {fmt!r}")
        args = ["pcb", "export", fmt]
        if include_tracks:
            args.append("--include-tracks")
        if include_zones:
            args.append("--include-zones")
        if subst_models:
            args.append("--subst-models")
        return self._to_file(args, board, output_file, timeout=600.0)

    def pcb_fabdata(self, board: str | Path, output_file: str | Path, *,
                    fmt: str = "ipc2581") -> Path:
        """Export a whole-package fab format: ipc2581, odb or gencad."""
        if fmt not in ("ipc2581", "odb", "gencad"):
            raise ValueError(
                f"fab format must be ipc2581, odb or gencad, not {fmt!r}")
        return self._to_file(["pcb", "export", fmt], board, output_file,
                             timeout=600.0)

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

    def pcb_import(self, source: str | Path, output_file: str | Path, *,
                   fmt: str = "easyeda") -> Path:
        """Import a non-KiCad board into KiCad format."""
        out = Path(output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        self._run(["pcb", "import", "--format", fmt, "-o", str(out),
                   str(_require(source, "file"))], timeout=600.0)
        return out

    def pcb_upgrade(self, board: str | Path) -> Path:
        """Migrate a board to the installed KiCad's file format."""
        path = _require(board, "board")
        self._run(["pcb", "upgrade", str(path)])
        return path

    # -- libraries --------------------------------------------------------

    def sym_svg(self, library: str | Path, output_dir: str | Path) -> Path:
        """Export a symbol library's symbols to SVG."""
        return self._to_dir(["sym", "export", "svg"], library, output_dir)

    def fp_svg(self, library: str | Path, output_dir: str | Path) -> Path:
        """Export a footprint library's footprints to SVG."""
        return self._to_dir(["fp", "export", "svg"], library, output_dir)

    def sym_upgrade(self, library: str | Path) -> Path:
        """Migrate a symbol library to the installed format."""
        path = _require(library, "library")
        self._run(["sym", "upgrade", str(path)])
        return path

    def fp_upgrade(self, library: str | Path) -> Path:
        """Migrate a footprint library to the installed format."""
        path = _require(library, "library")
        self._run(["fp", "upgrade", str(path)])
        return path

    def jobset(self, jobset_file: str | Path,
               project: str | Path | None = None) -> str:
        """Run a KiCad jobset and return its output."""
        args = ["jobset", "run", "--file", str(_require(jobset_file, "jobset"))]
        if project is not None:
            args += ["--project", str(project)]
        return self._run(args, timeout=600.0).stdout

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

    def _to_dir(self, args: list[str], source: str | Path,
                output_dir: str | Path, *, timeout: float = 180.0) -> Path:
        """Run *args* over *source* writing into *output_dir*; return it."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
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
