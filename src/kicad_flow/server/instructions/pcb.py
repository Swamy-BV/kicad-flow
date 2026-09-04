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

    "THE ONE THING TO UNDERSTAND: `place_footprint` returns its pads at their "
    "positions ON THE BOARD, with rotation and side already applied. Route to "
    "those numbers. A track drawn to where a pad would have been unrotated "
    "looks connected and is not. `get_pad` answers one pad; `get_footprint` "
    "returns them all for a part already placed.\n\n"

    "TWO WAYS A BOARD IS NOT A SHEET. It has LAYERS, so a position is not "
    "enough -- copper on the wrong layer connects nothing, and every copper "
    "call takes one. And a part can be on the BACK, where it is MIRRORED: its "
    "pads run the other way, so anything you routed to its old positions now "
    "goes nowhere.\n\n"

    "Workflow:\n"
    "1. `find_footprint` matches `Library:Footprint` ids -- search by package "
    "or family ('0603', 'LQFP-64'). `footprint_pads` gives the pads and the "
    "COURTYARD before you place anything. Use the courtyard to decide "
    "spacing: not the bounding box, which includes silkscreen, and not the "
    "pad extent, which excludes the body.\n"
    "2. `new_board` -- set the layer count HERE, or `set_board_layers` "
    "before any routing exists. Changing it afterwards invalidates the "
    "route. Then `add_outline` for the board edge.\n"
    "3. `place_footprint` for each part. `(x, y)` is the footprint ORIGIN, "
    "which on many parts sits at pad 1 rather than the middle; the reply's "
    "`courtyard_offset` is the vector to the courtyard centre, so add it to "
    "place by centre. Rotation is ANY angle. Then `move_footprint`, "
    "`rotate_footprint`, `flip_footprint` to adjust.\n"
    "4. `set_pad_net` for every pad. A library footprint carries NO nets -- "
    "it is a land pattern, not a circuit -- and without them the board is "
    "geometry: nothing reads as unrouted because nothing is connected, a "
    "plane joins nothing, and DRC calls every track a short. Which pad is on "
    "which net is a fact the SCHEMATIC holds: read it with `list_nets` on the "
    "sheet and apply it here.\n"
    "5. Copper: `add_track` lays ONE straight segment on ONE layer, so a "
    "corner is two calls and a layer change is `add_via`. `add_zone` pours a "
    "plane or -- with `forbids` -- fences a keep-out. A pour is not filled "
    "until `refill_zones`, and tracks laid after a pour do not update it. "
    "`remove_copper` is the undo for routing, filtered by net and layer; "
    "`remove_footprint` takes a part off the board.\n"
    "6. `save_board`, then CHECK -- see below.\n\n"

    "Reading back: `list_footprints` and `list_copper` give what is on the "
    "board, `list_board_nets` what it is meant to connect, "
    "`get_footprint_fields` a part's fields and `set_footprint_field` sets "
    "one. `add_board_text` puts a legend or a fab note on any layer -- "
    "back-side silkscreen wants `mirror` or it reads reversed.\n\n"

    "Checking, and do not skip it. `unrouted_connections` is the work "
    "remaining, NAMED nearest-first rather than counted, so you can route "
    "one; a filled plane counts as copper, so pads on a poured net are not "
    "reported. `check_board` runs DRC and names each violation by part and "
    "pad. `what_is_on_board` answers the same question one point at a time -- "
    "is this actually connected?\n\n"

    "Then LOOK AT IT. `render_board` writes a PNG, and it is the only thing "
    "that sees a designator printed over a pad, a part 6 mm from where you "
    "put it, or an outline that renders as one piece and would mill as three. "
    "Render BOTH sides: half the parts are usually on the back, and the "
    "bottom view is MIRRORED so left and right swap.\n\n"

    "Silkscreen: a library places each designator and cannot know what ends "
    "up beside it, and turning a part turns its label with it. On fine-pitch "
    "passives the reference is wider than the part it names. "
    "`move_footprint_field` moves one relative to its part, sets it upright "
    "again, or hides it -- which is usually what a 0402 wants.\n\n"

    "Two things this surface does NOT have, so do not look for them: nothing "
    "creates a board from a schematic in one call (place the footprints and "
    "apply the sheet's nets -- that composition is the point), and nothing "
    "sets design rules, so `check_board` grades against whatever the board "
    "was created with.\n\n"
)

__all__ = ["PCB"]
