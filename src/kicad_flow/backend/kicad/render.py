"""Render a schematic or a BOARD to an image.

This is the visual feedback loop: after generating or editing a design we
render it so the layout can be inspected. PNG is the form an agent can actually
*view*.

For schematics KiCad emits only SVG/PDF, so PNG is rasterized from a PDF with
PyMuPDF. For boards ``kicad-cli pcb render`` produces a PNG directly, which is
why :func:`render_board` exists as its own function -- and why it exists at
all: every board defect worth finding here was found by looking at one, and
until now nothing in this package could produce a picture of a board.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from kicad_flow.backend.kicad import cli as kicad_cli


def pdf_to_png(
    pdf: str | Path, out_dir: str | Path, stem: str, *, dpi: int = 200
) -> list[Path]:
    """Rasterize each page of *pdf* to ``<stem>[-N].png`` in *out_dir*.

    KiCad renders only SVG/PDF; this makes a PNG an agent can read back to
    actually see the schematic/board it produced.

    Raises:
        RuntimeError: If PyMuPDF (``pymupdf``) is not installed.
    """
    try:
        import pymupdf
    except ImportError as e:  # pragma: no cover - env-dependent
        raise RuntimeError(
            "PNG rendering needs PyMuPDF; install it with 'pip install pymupdf'"
        ) from e
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    zoom = dpi / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)
    written: list[Path] = []
    with pymupdf.open(str(pdf)) as doc:
        multi = doc.page_count > 1
        for i, page in enumerate(doc, start=1):
            name = f"{stem}-{i}.png" if multi else f"{stem}.png"
            dest = out / name
            page.get_pixmap(matrix=matrix).save(str(dest))
            written.append(dest)
    return written


def export_png(
    schematic: str | Path,
    *,
    output_dir: str | Path | None = None,
    dpi: int = 200,
    black_and_white: bool = False,
    exclude_drawing_sheet: bool = False,
    pages: str | None = None,
) -> list[Path]:
    """Render a schematic to PNG (one file per page) so it can be viewed.

    Exports a PDF via kicad-cli and rasterizes it at *dpi*. Returns the PNG
    paths, sorted.

    Raises:
        FileNotFoundError: If *schematic* does not exist.
        kicad_cli.KiCadCliError: If the export fails.
        RuntimeError: If PyMuPDF is not installed.
    """
    sch = Path(schematic)
    if not sch.is_file():
        raise FileNotFoundError(f"schematic not found: {sch}")
    out = Path(output_dir) if output_dir is not None else sch.parent
    with tempfile.TemporaryDirectory() as tmp:
        pdf = export_pdf(
            sch,
            output_file=Path(tmp) / f"{sch.stem}.pdf",
            black_and_white=black_and_white,
            exclude_drawing_sheet=exclude_drawing_sheet,
            pages=pages,
        )
        return sorted(pdf_to_png(pdf, out, sch.stem, dpi=dpi))


def export_pdf(
    schematic: str | Path,
    *,
    output_file: str | Path | None = None,
    black_and_white: bool = False,
    exclude_drawing_sheet: bool = False,
    pages: str | None = None,
) -> Path:
    """Export a schematic to a single PDF.

    Args:
        schematic: Path to the ``.kicad_sch`` file.
        output_file: Destination ``.pdf`` path; defaults to the schematic's
            name with a ``.pdf`` suffix, beside the schematic.
        black_and_white: Render without color.
        exclude_drawing_sheet: Omit the title-block/border frame.
        pages: Comma-separated page numbers; all pages if None.

    Returns:
        The path to the written PDF.

    Raises:
        FileNotFoundError: If *schematic* does not exist.
        kicad_cli.KiCadCliError: If the export fails.
    """
    sch = Path(schematic)
    if not sch.is_file():
        raise FileNotFoundError(f"schematic not found: {sch}")

    # Suffix so a schematic PDF never collides with a board PDF (-pcb) in the
    # same folder (both used to default to <name>.pdf).
    pdf = (
        Path(output_file)
        if output_file is not None
        else sch.with_name(f"{sch.stem}-sch.pdf")
    )
    pdf.parent.mkdir(parents=True, exist_ok=True)

    return kicad_cli.cli.sch_pdf(
        sch, pdf, black_and_white=black_and_white,
        exclude_drawing_sheet=exclude_drawing_sheet, pages=pages,
    )


def render_board(
    board: str | Path,
    output_file: str | Path,
    *,
    side: str = "top",
    width: int = 1200,
    height: int = 1200,
    background: str = "opaque",
    quality: str = "basic",
    zoom: float = 1.0,
    rotate: str | None = None,
    perspective: bool = False,
    floor: bool = False,
    pan: str | None = None,
    pivot: str | None = None,
) -> Path:
    """Render a board to a PNG that can actually be looked at.

    Args:
        board: Path to the ``.kicad_pcb``.
        output_file: PNG to write.
        side: ``top``, ``bottom``, ``left``, ``right``, ``front`` or ``back``.
        width: Image width in pixels.
        height: Image height in pixels.
        background: ``opaque``, ``transparent`` or ``default``.
        quality: ``basic`` (fast) or ``high``.
        zoom: Camera zoom; 1.0 fits the board.
        rotate: ``"X,Y,Z"`` degrees, e.g. ``"-45,0,45"`` for an isometric view.
        perspective: Use perspective rather than orthographic projection.
        floor: Draw a floor with shadows and post-processing.
        pan: Camera translation as ``"X,Y,Z"``.
        pivot: Orbit pivot relative to board centre as ``"X,Y,Z"`` centimetres.

    Returns:
        The PNG path.

    Raises:
        FileNotFoundError: If the board does not exist.
        ValueError: If *side* is not one of the six views.
        kicad_cli.KiCadCliError: If the render fails.
    """
    src = Path(board)
    if not src.is_file():
        raise FileNotFoundError(f"board not found: {src}")
    sides = ("top", "bottom", "left", "right", "front", "back")
    if side not in sides:
        raise ValueError(f"side must be one of {sides}, not {side!r}")
    backgrounds = ("opaque", "transparent", "default")
    if background not in backgrounds:
        raise ValueError(
            f"background must be one of {backgrounds}, not {background!r}")
    qualities = ("basic", "high", "user", "job_settings")
    if quality not in qualities:
        raise ValueError(f"quality must be one of {qualities}, not {quality!r}")
    if width < 1 or height < 1:
        raise ValueError("render width and height must be positive")
    if zoom <= 0:
        raise ValueError("render zoom must be positive")
    return kicad_cli.cli.pcb_render(
        src, output_file, side=side, width=int(width), height=int(height),
        quality=quality, rotate=rotate or None, background=background,
        zoom=zoom, perspective=perspective, floor=floor, pan=pan or None,
        pivot=pivot or None,
    )
