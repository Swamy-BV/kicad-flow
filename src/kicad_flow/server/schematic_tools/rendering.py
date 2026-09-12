"""Schematic MCP tools for rendering."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import _meta
from .._app import mcp
from .session import (
    _fail,
    _sheet,
)


def _pages(spec: str) -> list[int]:
    """A ``pages`` selector as page numbers, or a :class:`ValueError`.

    kicad-cli takes a comma-separated list and nothing else -- it answers
    ``"2-4"`` with "No sheets to plot", which reads like an empty design
    rather than a rejected argument. So the range is refused here, by name.
    """
    if not spec.strip():
        return []
    out = []
    for part in spec.split(","):
        token = part.strip()
        if not token.isdigit() or int(token) < 1:
            raise ValueError(
                f"pages must be a comma-separated list of page numbers, "
                f"e.g. '1,3,5' -- got {part.strip()!r}"
                + (" (there is no range syntax)" if "-" in token else "")
            )
        out.append(int(token))
    return out


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.WRITE)
def render_schematic(
    path: str,
    output_dir: str = "",
    dpi: int = 150,
    black_and_white: bool = False,
    pages: str = "",
    save: bool = True,
) -> dict[str, Any]:
    """Render the sheet to PNG -- one file per page -- so you can look at it.

    LOOK AT THE SHEET. `check_sheet` catches the electrical half and cannot
    see the other one: a label printed over a pin number, a power symbol
    through a net name, a wire drawn across text, a page that is correct and
    unreadable. Every readability fault found in this project so far was found
    by rendering and looking, and by nothing else -- ERC reported 0 errors and
    0 warnings for all of them.

    Args:
        path: The open sheet. On a hierarchy, render the ROOT to get every
            page; a child renders alone.
        output_dir: Where the PNGs go. Defaults to the sheet's own folder.
        dpi: Raster resolution. 150 reads a full A3 page; go higher and crop
            when checking one block for overprints.
        black_and_white: Render without colour.
        pages: Which pages, as a comma-separated list of page numbers --
            ``"3"`` or ``"1,3,5"``. Empty means all. There is no range
            syntax: kicad-cli refuses ``"2-4"`` with "No sheets to plot".
            Each PNG is named for the page it IS, not for its position in
            the selection.
        save: Write the sheet before rendering, so the picture is of what you
            have DRAWN rather than what was last saved. Leave it on unless you
            deliberately want the file as it stands on disk. Note the same
            caveat as `check_sheet`: a child sheet is read from disk, so save
            every child before rendering the root.

    Returns:
        ``{ok, path, count, images: [...]}``.
    """
    try:
        wanted = _pages(pages)
        sheet = _sheet(path)
        if save:
            sheet.save()
        images = sheet.render(
            output_dir=Path(output_dir) if output_dir else None,
            dpi=dpi,
            black_and_white=black_and_white,
            pages=pages or None,
        )
        # kicad-cli exports the SELECTION as a fresh document, so page 2 comes
        # back numbered 1 and a single page comes back with no number at all.
        # Left alone, `pages="2,3,4"` writes fc-1/fc-2/fc-3 holding pages
        # 2/3/4 -- names that are wrong rather than merely unhelpful.
        if wanted:
            # kicad-cli emits the selection in DOCUMENT order however it was
            # listed, so `pages="5,1"` comes back as page 1 then page 5 and
            # pairing with the given order labels both of them wrongly.
            wanted = sorted(set(wanted))
            # And through temporaries, because the selection is renumbered
            # from 1: renaming in place walks fc-1 onto the fc-2 that has not
            # been renamed yet, and the second file is lost.
            stem = sheet.path.stem
            staged = [
                (f.replace(f.with_name(f".{f.name}.tmp")), f.suffix) for f in images
            ]
            images = [
                f.replace(f.with_name(f"{stem}-{n}{suffix}"))
                for (f, suffix), n in zip(staged, wanted, strict=False)
            ]
    except (LookupError, OSError, RuntimeError, ValueError) as exc:
        return _fail(exc)
    numbers = wanted or list(range(1, len(images) + 1))
    return {
        "ok": True,
        "path": str(sheet.path),
        "count": len(images),
        "pages": numbers[: len(images)],
        "images": [str(p) for p in images],
    }
