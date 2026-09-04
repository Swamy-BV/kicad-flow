"""Find and read KiCad footprint libraries.

A ``.pretty`` library is a folder of ``.kicad_mod`` files, and a ``.kicad_mod``
is the same S-expression dialect as everything else here -- so a footprint is
read directly rather than through pcbnew, and placing one is a tree copy rather
than a subprocess.

Which folders exist is environment work: KiCad keeps a global ``fp-lib-table``
mapping nicknames to paths, with variables to expand. That resolution lives
here because it is entirely a fact about an installed KiCad -- nothing above
the backend has any use for it.
"""

from __future__ import annotations

import math
import os
import re
from functools import cache
from pathlib import Path

from .._sexpr import Node, loads
from ._runner import footprints_dir


def _global_fp_tables() -> list[Path]:
    """Candidate global ``fp-lib-table`` files in KiCad's config dir."""
    out: list[Path] = []
    appdata = os.environ.get("APPDATA")  # Windows
    roots = [Path(appdata) / "kicad"] if appdata else []
    roots.append(Path.home() / ".config" / "kicad")  # Linux/macOS
    for root in roots:
        if root.is_dir():
            out.extend(sorted(root.glob("*/fp-lib-table")))
            if (root / "fp-lib-table").is_file():
                out.append(root / "fp-lib-table")
    return out


def _expand(uri: str, project_dir: Path | None) -> Path:
    """Expand a fp-lib-table URI's KiCad/env variables to a filesystem path."""
    stock = footprints_dir()
    text = uri
    if project_dir is not None:
        text = text.replace("${KIPRJMOD}", str(project_dir))
    # Any KICAD*_FOOTPRINT_DIR points at the stock footprints root.
    for key, val in os.environ.items():
        if key.endswith("_FOOTPRINT_DIR"):
            text = text.replace(f"${{{key}}}", val)
    # A function repl avoids backslashes in the Windows path being read as
    # regex escapes in the replacement string.
    text = re.sub(r"\$\{KICAD\d*_FOOTPRINT_DIR\}", lambda _m: str(stock), text)
    return Path(os.path.expandvars(text))


def _parse_fp_table(path: Path, project_dir: Path | None) -> dict[str, Path]:
    """Parse a ``fp-lib-table`` into ``{nickname: .pretty path}``."""
    try:
        root = loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    libs: dict[str, Path] = {}
    for lib in root.get_all("lib"):
        name = lib.get("name")
        uri = lib.get("uri")
        if name is None or uri is None or len(name.items) < 2 or len(uri.items) < 2:
            continue
        libs[str(name.items[1])] = _expand(str(uri.items[1]), project_dir)
    return libs


@cache
def _libraries() -> dict[str, Path]:
    """Every footprint-library nickname mapped to its ``.pretty`` folder.

    Merges (later wins): KiCad's stock ``.pretty`` dirs, then the global
    ``fp-lib-table``(s).
    """
    libs: dict[str, Path] = {}
    stock = footprints_dir()
    if stock.is_dir():
        for d in stock.glob("*.pretty"):
            libs[d.stem] = d
    for tbl in _global_fp_tables():
        libs.update(_parse_fp_table(tbl, None))
    return libs


def search(query: str, limit: int = 20) -> list[str]:
    """``Library:Footprint`` ids containing *query*, case-insensitively.

    Matching is on the id alone. Reading every footprint's description would
    mean opening a few thousand files; search by family or package --
    ``"0603"``, ``"LQFP-64"``, ``"PinHeader_1x06"``.
    """
    needle = query.lower()
    out: list[str] = []
    for nickname, folder in sorted(_libraries().items()):
        if not folder.is_dir():
            continue
        for file in sorted(folder.glob("*.kicad_mod")):
            fp_id = f"{nickname}:{file.stem}"
            if needle in fp_id.lower():
                out.append(fp_id)
                if len(out) >= limit:
                    return out
    return out


def load(fp_id: str) -> Node:
    """Parse ``Library:Footprint`` and return its tree.

    Raises:
        LookupError: If the library or the footprint is not on disk.
    """
    if ":" not in fp_id:
        raise LookupError(f"footprint id must be 'Library:Footprint', "
                          f"not {fp_id!r}")
    nickname, name = fp_id.split(":", 1)
    folder = _libraries().get(nickname)
    if folder is None:
        raise LookupError(f"no footprint library {nickname!r}")
    file = folder / f"{name}.kicad_mod"
    if not file.is_file():
        raise LookupError(f"no footprint {fp_id!r} at {file}")
    return loads(file.read_text(encoding="utf-8"))


def _extent(shape: Node, kind: str) -> tuple[list[float], list[float]]:
    """Every x and y a drawn shape actually reaches.

    A circle is stored as its centre and ONE point on the rim, so reading those
    two as corner points gives half the width and no height at all: KiCad's
    ``MountingHole_3.2mm_M3_Pad`` draws a 6.9 mm round courtyard and measured
    ``3.45 x 0.0``. It is expanded to centre +/- radius here instead.

    An arc still contributes only its three stored points, which understates
    the bulge between them.
    """
    if kind == "fp_circle":
        centre, rim = shape.get("center"), shape.get("end")
        if centre is not None and rim is not None:
            cx, cy = float(str(centre.items[1])), float(str(centre.items[2]))
            rx, ry = float(str(rim.items[1])), float(str(rim.items[2]))
            r = math.hypot(rx - cx, ry - cy)
            return [cx - r, cx + r], [cy - r, cy + r]
    xs: list[float] = []
    ys: list[float] = []
    for corner in ("start", "end", "center", "mid", "at"):
        node = shape.get(corner)
        if node is not None and len(node.items) >= 3:
            xs.append(float(str(node.items[1])))
            ys.append(float(str(node.items[2])))
    pts = shape.get("pts")
    for xy in (pts.get_all("xy") if pts is not None else []):
        xs.append(float(str(xy.items[1])))
        ys.append(float(str(xy.items[2])))
    return xs, ys


def courtyard_box(tree: Node) -> tuple[float, float, float, float]:
    """``(width, height, centre_x, centre_y)`` of the courtyard, in mm.

    The courtyard is the room a part actually needs -- not the bounding box,
    which includes silkscreen, and not the pad extent, which excludes the
    body. Falls back to the pad extent for a footprint that draws none.
    """
    xs: list[float] = []
    ys: list[float] = []
    for kind in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
        for shape in tree.get_all(kind):
            layer = shape.get("layer")
            if layer is None or not str(layer.items[1]).endswith(".CrtYd"):
                continue
            sx, sy = _extent(shape, kind)
            xs += sx
            ys += sy
    if not xs:
        for pad in tree.get_all("pad"):
            at = pad.get("at")
            size = pad.get("size")
            if at is None or size is None:
                continue
            px, py = float(str(at.items[1])), float(str(at.items[2]))
            sw, sh = float(str(size.items[1])), float(str(size.items[2]))
            xs += [px - sw / 2, px + sw / 2]
            ys += [py - sh / 2, py + sh / 2]
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3),
            round((max(xs) + min(xs)) / 2, 3),
            round((max(ys) + min(ys)) / 2, 3))


def courtyard(tree: Node) -> tuple[float, float]:
    """The courtyard's ``(width, height)``."""
    w, h, _, _ = courtyard_box(tree)
    return (w, h)


def bbox(tree: Node) -> tuple[float, float]:
    """Everything the footprint draws, on every layer, as ``(width, height)``."""
    xs: list[float] = []
    ys: list[float] = []
    for kind in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc", "pad"):
        for shape in tree.get_all(kind):
            sx, sy = _extent(shape, kind)
            xs += sx
            ys += sy
    if not xs:
        return (0.0, 0.0)
    return (round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3))


__all__ = ["bbox", "courtyard", "courtyard_box", "load", "search"]
