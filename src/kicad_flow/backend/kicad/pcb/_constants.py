"""KiCad board format constants, native scripts and the empty-board template."""

from __future__ import annotations

_COPPER_COUNTS = (2, 4, 6, 8)


def _copper_names(count: int) -> tuple[str, ...]:
    """Copper names in physical order for a supported rigid board."""
    if count not in _COPPER_COUNTS:
        raise ValueError(
            f"layer count must be one of {list(_COPPER_COUNTS)}, not {count}"
        )
    return ("F.Cu", *(f"In{i}.Cu" for i in range(1, count - 1)), "B.Cu")


_SIDES = ("F", "B")


_GRAPHIC_NODES = {
    "line": "gr_line",
    "arc": "gr_arc",
    "circle": "gr_circle",
    "rectangle": "gr_rect",
    "polygon": "gr_poly",
}


_GRAPHIC_KINDS = {node: kind for kind, node in _GRAPHIC_NODES.items()}


_GRAPHIC_LAYERS = ("Edge.Cuts", "F.SilkS", "B.SilkS")


_STACKUP_KINDS = {
    "copper",
    "core",
    "prepreg",
    "Top Silk Screen",
    "Top Solder Paste",
    "Top Solder Mask",
    "Bottom Solder Mask",
    "Bottom Solder Paste",
    "Bottom Silk Screen",
}


_VIA_KINDS = {"through": "", "blind_buried": "blind", "microvia": "micro"}


_ISLAND_MODES = {"always": 0, "never": 1, "area": 2}


_ISLAND_MODES_BY_NUMBER = {value: key for key, value in _ISLAND_MODES.items()}


_REFILL = r"""
import json, sys
import pcbnew

job = json.load(open(sys.argv[1], encoding="utf-8"))
board = pcbnew.LoadBoard(job["board_path"])
zones = list(board.Zones())
save_board(board, job["board_path"])
print(json.dumps({"ok": True, "zones": len(zones)}))
"""


_OUTLINE_POLYGON = r"""
import json, sys
import pcbnew

job = json.load(open(sys.argv[1], encoding="utf-8"))
board = pcbnew.LoadBoard(job["board_path"])
error = pcbnew.FromMM(float(job["max_error"]))
board.GetDesignSettings().m_MaxError = error
outlines = pcbnew.SHAPE_POLY_SET()
valid = board.GetBoardPolygonOutlines(
    outlines, False, None, True, False
)
if not valid:
    raise ValueError("Edge.Cuts is not a valid closed board outline")
if outlines.OutlineCount() != 1:
    raise ValueError(
        "board-outline zones require exactly one outside contour; found "
        + str(outlines.OutlineCount())
    )
inset = pcbnew.FromMM(float(job["inset"]))
if inset:
    outlines.Deflate(
        inset, pcbnew.CORNER_STRATEGY_ALLOW_ACUTE_CORNERS, error
    )
if outlines.OutlineCount() != 1:
    raise ValueError("board outline collapsed or split under the requested inset")
chain = outlines.COutline(0)
points = [[mm(chain.CPoint(i).x), mm(chain.CPoint(i).y)]
          for i in range(chain.PointCount())]
print(json.dumps({"ok": True, "points": points}))
"""


_TEMPLATE = """(kicad_pcb
\t(version 20260206)
\t(generator "kicad_flow")
\t(generator_version "10.0")
\t(general
\t\t(thickness 1.6)
\t\t(legacy_teardrops no)
\t)
\t(paper "A4")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(9 "F.Adhes" user "F.Adhesive")
\t\t(11 "B.Adhes" user "B.Adhesive")
\t\t(13 "F.Paste" user)
\t\t(15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(7 "B.SilkS" user "B.Silkscreen")
\t\t(1 "F.Mask" user)
\t\t(3 "B.Mask" user)
\t\t(17 "Dwgs.User" user "User.Drawings")
\t\t(19 "Cmts.User" user "User.Comments")
\t\t(21 "Eco1.User" user "User.Eco1")
\t\t(23 "Eco2.User" user "User.Eco2")
\t\t(25 "Edge.Cuts" user)
\t\t(27 "Margin" user)
\t\t(31 "F.CrtYd" user "F.Courtyard")
\t\t(29 "B.CrtYd" user "B.Courtyard")
\t\t(35 "F.Fab" user)
\t\t(33 "B.Fab" user)
\t)
\t(setup
\t\t(pad_to_mask_clearance 0)
\t\t(allow_soldermask_bridges_in_footprints no)
\t\t(pcbplotparams
\t\t\t(layerselection 0x00000000_00000000_55555555_5755f5ff)
\t\t\t(plot_on_all_layers_selection 0x00000000_00000000_00000000_00000000)
\t\t\t(disableapertmacros no)
\t\t\t(usegerberextensions no)
\t\t\t(usegerberattributes yes)
\t\t\t(usegerberadvancedattributes yes)
\t\t\t(creategerberjobfile yes)
\t\t\t(dashed_line_dash_ratio 12.000000)
\t\t\t(dashed_line_gap_ratio 3.000000)
\t\t\t(svgprecision 4)
\t\t\t(plotframeref no)
\t\t\t(mode 1)
\t\t\t(useauxorigin no)
\t\t\t(dxfpolygonmode yes)
\t\t\t(dxfimperialunits yes)
\t\t\t(dxfusepcbnewfont yes)
\t\t\t(psnegative no)
\t\t\t(psa4output no)
\t\t\t(plot_black_and_white yes)
\t\t\t(sketchpadsonfab no)
\t\t\t(plotpadnumbers no)
\t\t\t(hidednponfab no)
\t\t\t(sketchdnponfab yes)
\t\t\t(crossoutdnponfab yes)
\t\t\t(subtractmaskfromsilk no)
\t\t\t(outputformat 1)
\t\t\t(mirror no)
\t\t\t(drillshape 1)
\t\t\t(scaleselection 1)
\t\t\t(outputdirectory "")
\t\t)
\t)
\t(embedded_fonts no)
)
"""
