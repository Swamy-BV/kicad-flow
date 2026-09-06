"""The board half of the server instructions.

Rewritten against the tools that exist. The text this replaces described
`export_pcb`, `place_board`, `route_board`, `apply_process` and a hint
vocabulary -- a surface that decided where parts went and how tracks ran, and
that has been removed along with the schematic floorplanner before it. Every
tool named below was checked against ``mcp.list_tools()``.
"""

from __future__ import annotations

#: How to lay out a board with the primitives, and nothing above them.
PCB = (
    "Boards, same rules as the schematic: primitives only. There is no "
    "autoplacer, no router, no via stitcher and no fanout -- those decided "
    "things you could not override. YOU decide where each part goes and how "
    "each track runs; the server tells you where things ended up.\n\n"

    "THE ONE THING TO UNDERSTAND: every repeatable write takes a LIST, and "
    "one item is a list of one. `place_footprints` returns each part's pads at their "
    "positions ON THE BOARD, with rotation and side already applied. Route to "
    "those numbers. A track drawn to where a pad would have been unrotated "
    "looks connected and is not. `get_pad` answers one pad; `get_footprint` "
    "returns them all for a part already placed.\n\n"

    "TWO WAYS A BOARD IS NOT A SHEET. It has LAYERS, so a position is not "
    "enough -- copper on the wrong layer connects nothing, and every copper "
    "call takes one. And a part can be on the BACK, where it is MIRRORED: its "
    "pads run the other way, so anything you routed to its old positions now "
    "goes nowhere.\n\n"

    "Every coordinate, size, clearance, drill, width and stackup thickness is "
    "in millimetres. Keep the design in millimetres; do not convert values to "
    "mils before calling the tools.\n\n"

    "Workflow:\n"
    "1. `find_footprint` matches `Library:Footprint` ids -- search by package "
    "or family ('0603', 'LQFP-64'). `footprint_pads` gives the pads and the "
    "COURTYARD before you place anything. Use the courtyard to decide "
    "spacing: not the bounding box, which includes silkscreen, and not the "
    "pad extent, which excludes the body.\n"
    "2. `new_board` -- set 2, 4, 6 or 8 layers HERE, or `set_board_layers` "
    "before any routing exists. Changing it afterwards invalidates the "
    "route. Then `add_graphics` on `Edge.Cuts` for the board edge. Lines "
    "and arcs compose complex contours; circles, rectangles and polygons "
    "are closed contours themselves. Set the physical construction with "
    "`set_stackup`; its copper entries must match the board layers exactly.\n"
    "3. `place_footprints` for all parts. `(x, y)` is the footprint ORIGIN, "
    "which on many parts sits at pad 1 rather than the middle; the reply's "
    "`courtyard_offset` is the vector to the courtyard centre, so add it to "
    "place by centre. Rotation is ANY angle. Then `move_footprints`, "
    "`rotate_footprints`, `flip_footprints` to adjust.\n"
    "4. `set_pad_nets` for every pad. A library footprint carries NO nets -- "
    "it is a land pattern, not a circuit -- and without them the board is "
    "geometry: nothing reads as unrouted because nothing is connected, a "
    "plane joins nothing, and DRC calls every track a short. Which pad is on "
    "which net is a fact the SCHEMATIC holds: read it with `list_nets` on the "
    "sheet and apply it here.\n"
    "5. Before routing, select manufacturing policy. For a provider build, "
    "call `get_fabrication_capabilities` for the provider's exact choices, "
    "then `set_fabrication_profile` immediately after `new_board`; it reads "
    "the actual layer count and thickness, rejects incompatible explicit "
    "choices, and applies neutral board limits without changing geometry. "
    "Use `get_fabrication_profile` and `get_board_limits` to read it back. "
    "Without a provider, `set_board_limits` sets those limits directly. Then "
    "use `set_net_classes` and `assign_net_classes` for "
    "shared trace/via/differential-pair dimensions. Use "
    "`set_board_constraints` for conditional limits such as width, gap, "
    "uncoupled length, total length and skew. These record dimensions; they "
    "do not calculate impedance from the stackup.\n"
    "6. Copper: each item in `add_tracks` is ONE straight segment on ONE layer, so a "
    "corner is two items and a layer change is `add_vias`. `add_zones` pours a "
    "plane or -- with `forbids` -- fences a keep-out. Supply polygon points, "
    "or use `boundary=\"board_outline\"` with an explicit inset to follow "
    "line-and-arc Edge.Cuts at a bounded curve error. A pour is not filled "
    "until `refill_zones`, and tracks laid after a pour do not update it. "
    "`remove_copper` is the undo for routing, filtered by net and layer; "
    "`remove_footprints` takes parts off the board.\n"
    "7. `save_board`, then CHECK -- see below.\n\n"

    "Reading back: `list_footprints` and `list_copper` give what is on the "
    "board, `list_board_nets` what it is meant to connect, "
    "`get_footprint_fields` a part's fields and `set_footprint_fields` sets "
    "them. `add_graphics` draws line, arc, circle, rectangle and polygon "
    "art on front/back silkscreen; `list_graphics`, `move_graphics` and "
    "`remove_graphics` make those shapes editable. `add_board_texts` puts "
    "legends or fab notes on any layer -- "
    "back-side silkscreen wants `mirror` or it reads reversed. `get_stackup`, "
    "`list_net_classes`, `list_net_class_assignments` and "
    "`list_board_constraints` read the manufacturing and routing policy back.\n\n"

    "Checking, and do not skip it. `unrouted_connections` is the work "
    "remaining, NAMED nearest-first rather than counted, so you can route "
    "one; a filled plane counts as copper, so pads on a poured net are not "
    "reported. `check_board` runs DRC and names each violation by part and "
    "pad. `what_is_on_board` answers the same question one point at a time -- "
    "is this actually connected?\n\n"

    "Then LOOK AT IT. `render_board` writes a 3D PNG or JPEG, and it is the only thing "
    "that sees a designator printed over a pad, a part 6 mm from where you "
    "put it, or an outline that renders as one piece and would mill as three. "
    "Render BOTH sides: half the parts are usually on the back, and the "
    "bottom view is MIRRORED so left and right swap. For an assembly view, "
    "use `rotate='-30,0,25'`, `perspective=true`, and optionally `floor=true`; "
    "quality, background, zoom, pan and pivot are explicit caller choices.\n\n"

    "Silkscreen: a library places each designator and cannot know what ends "
    "up beside it, and turning a part turns its label with it. On fine-pitch "
    "passives the reference is wider than the part it names. "
    "`move_footprint_fields` moves them relative to their parts, sets them upright "
    "again, or hides it -- which is usually what a 0402 wants.\n\n"

    "One thing this surface does NOT have, so do not look for it: nothing "
    "creates a board from a schematic in one call (place the footprints and "
    "apply the sheet's nets -- that composition is the point).\n\n"
)

__all__ = ["PCB"]
