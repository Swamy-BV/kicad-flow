"""The schematic half of the server instructions.

Rewritten against the tools that exist. The text this replaces described a
declarative ``apply_design`` document, a floorplanner and a research gate --
none of which are here any more -- and of the 27 schematic tools it named
exactly one. Every tool named below was checked against ``mcp.list_tools()``.

Keep it that way: this file is guidance for a caller, so a name in it that no
tool answers to is the same defect as a broken call.
"""

from __future__ import annotations

#: How to author a sheet with the primitives, and nothing above them.
SCHEMATIC = (
    "Author KiCad 10 schematics from primitives. There is no design document, "
    "no autoplacer and no router: every call does one thing, takes scalars, "
    "and returns what it made. YOU decide where a part goes and where a wire "
    "turns; the server tells you where things ended up.\n\n"

    "THE ONE THING TO UNDERSTAND: `add_component` returns its pins at their "
    "positions ON THE SHEET, with rotation and mirroring already applied. Wire "
    "to those numbers. A wire drawn to where a pin would have been if the part "
    "were unrotated looks connected, is not, and nothing on the sheet says so. "
    "`get_component` returns the same pins for a part already placed, and "
    "`get_pin` answers one by number or name.\n\n"

    "Everything is millimetres from the top-left and is snapped to the 1.27 mm "
    "grid. Two points that differ by a fraction of a millimetre are not "
    "connected.\n\n"

    "Workflow:\n"
    "1. `find_symbol` matches against `Library:Symbol` ids only -- search by "
    "part number or family (`AP2112`, `USB_C_Receptacle`), not by prose. "
    "`symbol_pins` gives a symbol's pins and size BEFORE placing, which is how "
    "you work out how much room to leave.\n"
    "2. `new_sheet` -- A4 by default, up to A0. Nothing is on disk until "
    "`save_sheet`.\n"
    "3. `add_component` for each part; `next_ref` if you would rather not keep "
    "a counter. `rotation` must be 0, 90, 180 or 270 -- any other angle writes "
    "a file KiCad refuses to open, and the call will not stop you. `mirror` is "
    "'x', 'y' or empty. A multi-unit symbol (an LM358 is two op-amps plus a "
    "shared power unit) is placed ONE UNIT PER CALL, all units sharing one "
    "ref; keep the pins each call returned, because `get_pin`, `set_field`, "
    "`move_field` and `get_fields` only ever see unit 1.\n"
    "4. Wire it: `add_wire` draws ONE straight segment, so a corner is two "
    "calls. `add_junction` where a wire T's or crosses another it must join -- "
    "wires that cross without one are NOT connected. `add_label` names a net: "
    "two labels with the same text are one net with no wire between them.\n"
    "5. `add_power` for a rail (GND, +3V3, +5V) -- its single returned pin is "
    "what you wire to. `add_power_flag` on each supply, or ERC reports every "
    "rail as undriven. `add_no_connect` on a pin left unconnected on purpose.\n"
    "6. `set_field(ref, 'Footprint', ...)` is how a part gets a footprint. "
    "There is no separate call: a footprint is a field with a name the board "
    "export happens to read. `Value`, `Datasheet`, `MPN` work the same way.\n"
    "7. `save_sheet`, then CHECK -- see below.\n\n"

    "Checking, and do not skip it: `list_nets` is the only call that tells you "
    "what the sheet ACTUALLY connects, read back from KiCad itself rather than "
    "from what you believe you drew. A schematic can be a valid file that "
    "opens and renders correctly while its wires join nothing. `check_sheet` "
    "runs ERC and names each violation by part and pin. `what_is_at` answers "
    "the same question one point at a time -- is this actually connected? -- "
    "and is what to reach for when a net came out wrong.\n\n"

    "Then LOOK AT IT. `render_schematic` writes a PNG per page. Neither ERC "
    "nor the netlist can see a label printed over a pin number, a power symbol "
    "through a net name, a wire drawn across text, or a page that is correct "
    "and unreadable. Every readability fault found in this project was found "
    "by rendering and looking, and by nothing else -- ERC reported 0 errors "
    "and 0 warnings for all of them. It saves the sheet first, so the picture "
    "is of what you drew rather than what was last written.\n\n"

    "Hierarchy, for a design too big for one page:\n"
    "* `add_sheet` puts a child box on the root and returns where its ports "
    "landed and an `instance_path`.\n"
    "* Create the child with `new_sheet`, PASSING THAT `instance_path`. Get it "
    "wrong and the child's parts annotate against the wrong sheet, their nets "
    "never merge into the design, and nothing says so.\n"
    "* Signals cross by NAME: a port on the box, and `add_label` with "
    "`kind='hierarchical'` and the SAME name inside the child. Nothing checks "
    "the pairing while you draw.\n"
    "* Power does not need ports. `kind='global'` labels and power symbols "
    "join across the whole design wherever they are.\n"
    "* `save_sheet` EVERY child before `list_nets` or `check_sheet` on the "
    "root. Those two read children from disk, so an unsaved child is reported "
    "as though its parts were not there, with no warning.\n\n"

    "Labels: `justify` is what POINTS a global or hierarchical label, not "
    "`rotation`. Use 'right' on a part's LEFT-hand pins -- it puts the flag's "
    "tip on the right where the wire arrives and grows the box away from the "
    "part -- and 'left' on its right-hand pins. Backwards, and the wire runs "
    "through the text. `rotation` only matters for a vertical label (90, 270): "
    "a horizontal one renders identically at 0 and 180.\n\n"

    "Conventions worth following: signal flows left to right; power symbols "
    "point up and grounds down; wire locally inside a functional block and use "
    "labels to cross between blocks; a decoupling cap sits 2.54-5 mm from the "
    "pin it serves. None of this is enforced -- it is what makes a sheet "
    "readable, and only you can see the sheet.\n\n"

    "Reference and Value are placed automatically, clear of the side the "
    "part's wires leave from. That is a default, not a rule: on a crowded "
    "sheet two parts' labels can still meet, and `move_field` is how one gives "
    "way. `list_components`, `list_wires` and `get_fields` read back what is "
    "there.\n\n"

    "To change a part after placing it: `move_component`, `rotate_component`, "
    "`mirror_component`, `remove_component`. Each returns the part with its "
    "pins where they NOW are, which is the reason to use one rather than "
    "removing and re-placing. They do not move the wires you already drew.\n\n"

    "A sheet stays open in the server between calls, keyed by its path, so a "
    "session is a sequence of small calls rather than a re-parse each time. "
    "Work in batches -- every call is a full round-trip -- and check at "
    "milestones rather than after every wire.\n\n"
)

__all__ = ["SCHEMATIC"]
