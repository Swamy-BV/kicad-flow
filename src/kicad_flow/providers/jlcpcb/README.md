# JLCPCB parts provider

This provider searches a local snapshot of JLCPCB/LCSC assembly parts. It does
not call JLCPCB during a design session and it does not generate a BOM yet.
Stock and prices are therefore the values at the snapshot time; confirm them
before ordering.

## Install the catalogue

Download the SQLite database directly from CDFER:

<https://cdfer.github.io/jlcpcb-parts-database/jlcpcb-components.sqlite3>

Save it at this exact project-relative path and filename:

```text
src/kicad_flow/providers/jlcpcb/data/jlcpcb-components.sqlite3
```

Do not commit the file. The `data/` directory ignores downloaded content while
keeping its instructions in Git. After copying it into place, call
`get_parts_provider_status(provider="jlcpcb")` to confirm that KiCadFlow can
open it.

The database is published by the MIT-licensed community project
<https://github.com/CDFER/jlcpcb-parts-database>, generated from
<https://github.com/yaqwsx/jlcparts>. It is not an official JLCPCB service.

## MCP workflow

1. Call `get_parts_provider_status(provider="jlcpcb")`.
2. Call `search_parts` with the desired text and explicit package, maker,
   assembly-class, and stock filters.
3. Use the returned LCSC number and manufacturer part number in the design.
4. If KiCad has no suitable CAD model, download a KiCad bundle from SnapMagic
   (formerly SnapEDA) or Component Search Engine in an interactive browser.
   Never send account passwords through an MCP argument.
5. Call `import_project_libraries` with the downloaded ZIP or extracted folder.
   Its symbol, footprint, and optional STEP/WRL model are copied into that
   project's `kicad-flow-libraries/` folder and registered in the project's
   library tables. Nothing is installed in KiCad's global libraries.

Third-party CAD assets are starting points, not verified truth. Compare pin
numbers, pad dimensions, courtyard, orientation, and 3D alignment against the
manufacturer datasheet before fabrication.

## Rigid FR-4 fabrication profile

The checked-in `capabilities.json` snapshot contains JLCPCB's published rigid
FR-4 limits retrieved on 2026-09-05. The initial profile deliberately covers
2-, 4-, 6- and 8-layer boards, routed outlines and through-vias only.

Call `set_fabrication_profile` immediately after `new_board`, supplying copper
weight, finish, soldermask color, impedance-control choice and either the
`recommended` or `minimum` tier. Layer count and thickness are read from the
board because they are already facts. The call rejects unsupported combinations
before layout, applies provider-neutral board limits, and records its source in
the project-local `<board>.kicad-flow.json` file. `check_board` then includes
provider board-size findings alongside KiCad DRC findings.

The snapshot is not fetched at runtime. Review it against the current official
capability table before production:
<https://jlcpcb.com/capabilities/pcb-capabilities/>.
