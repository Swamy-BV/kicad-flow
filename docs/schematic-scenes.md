# Spatial context without images

`inspect_schematic_scene` observes a schematic without rendering or saving it.
It returns stable IDs, bounds, pin anchors and conservative spatial findings.
An AI can inspect a functional region and revise explicit placement lists using
these facts. Placement and wiring remain separate existing tool calls.

## Query a sheet or region

```json
{"path": "project/power.kicad_sch"}
```

For an explicitly chosen rectangle, supply all of `x1`, `y1`, `x2`, `y2`.
Coordinates are millimetres from the top-left, Y increases downward, and query
coordinates snap to the schematic grid. Objects are selected by inclusive
bounds intersection. They are returned whole, including wires extending outside
the rectangle; a `parent_id` may refer to an object outside the selected view.
Select a larger region when those parents or interfaces are needed.

The tool reads the current in-memory sheet. It does not walk child sheets or
implicitly choose a functional block. Sheet-box properties include the child
filename and instance path. Resolve relative child filenames against the parent
directory and query those paths explicitly. Native hierarchy verification still
uses `list_nets` and `check_sheet` on the root after saving the children.

## Object identity and geometry

Every object has `id`, `kind`, `bounds`, `x`, `y`, and scalar `properties`.
Bounds are `[left, top, right, bottom]`. Components have references, units and
symbol IDs. Pins have numbers, names, electrical types and transformed connection
anchors. Pins and visible fields carry their component's `parent_id`.
Lines carry `points`; labels carry `label_kind` and text.

The current implementation covers symbol bounds, pins, visible fields, labels,
notes, wires, junctions, no-connects, child-sheet boxes and ports, buses, bus
entries and polylines. Native UUIDs identify source objects; pin and field IDs
derive from their parent's UUID. IDs survive moves, rotations and reloads.
Deleting and adding an object can change identity. Missing or duplicate required
identities are refused, not silently repaired by a read.

Scene IDs are readback identities, not a second editing API. Use the returned
reference, unit and coordinates with the existing plural editing tools.

## Incremental updates

Keep the returned `revision`. On the next query use the same path and rectangle
and pass that value as `since`.

- `mode: "full"`: replace local objects and findings with the returned lists.
- `mode: "delta"`: upsert `objects` and `findings` by ID, then remove the IDs in
  `removed` and `removed_findings`.
- `base_revision` identifies the observation a delta applies to. Reject a delta
  if it does not match the client's current revision.
- `revision` is an opaque content cursor for this observation, not a monotonically
  increasing project counter. Changes outside the selected region need not change
  it. Identical observed content can reuse a revision.
- An unknown, evicted, restarted-server or different-scope cursor returns a full
  response with `reset: true`, never a partial diff. Each client retains its own
  cursor. Reusing a valid cursor does not consume it.

A removed ID may mean that an object moved outside the queried region; it does
not necessarily mean the object was deleted from the design.

`object_count` and `finding_count` describe the entire current selection, not
just the number of changed entries. Empty change lists mean the observation is
unchanged; they do not mean the sheet is empty.

## Bounds and limits

`max_objects` limits the selected scene. Over-budget requests fail explicitly;
reduce the region or raise the limit within the documented tool bounds. History
is bounded across all paths and clients. A snapshot too large for history can
still be returned, but a later request may require a full reset.

Extraction makes a pass through the selected sheet's source objects; it does not
load the complete hierarchy. Region queries bound returned context, not extraction
work. Collision candidates use a sweep of bounds. This first implementation does
not maintain a persistent spatial index or solve layout. Dense overlapping regions
can still be expensive. No large-design latency guarantee is claimed.

## What the findings mean

Findings name stable IDs for potential body overlaps, text overlaps and text
crossed by wires. Text bounds reuse the backend's conservative font-width
estimates. A label touching its own wire is excluded from text-wire findings.
Same-component bodies and fields are excluded from internal overlap checks.

`geometry_only: true`, `text_bounds: "estimated"` and
`connectivity_verified: false` are deliberate. This is not ERC, a complete
readability proof, an intended netlist or electrical connectivity. Pin names and
label text are observed properties, not inferred net memberships. Use native
netlist and ERC tools separately. Spatially touching pins and wires are not
automatically reported as an electrical connection.

`unsupported_kinds` lists unmodeled root-level objects, such as imported drawing
types. Library symbol artwork is represented by its bounds and pins, not by
every native stroke. Hidden fields, title-block artwork and embedded images are
not part of this geometry view. The scene is not a pixel-perfect schematic render.

## Human view

The live monitor's **Geometry** button is available for an active schematic.
It draws these same structured objects as SVG, highlights potential overlaps,
and shows an object's ID and bounds when selected. It receives full/delta JSON;
it does not fetch raster previews while Geometry is selected. Native 2D and board
3D views remain available. The existing background preview worker remains active.

The monitor reads saved files; MCP reads its open in-memory sheet. They describe
the same revision after a successful save, but autosave failure or external editor
changes can make those states differ. This feature does not resolve existing
multi-writer ownership or external-edit conflicts; use one design writer.

## Verification

Run `python examples/scripts/scene.py`. This example checks observations through
`Client(mcp)`, including transformed pins, multi-unit identities, region exits,
cursor reset, rollback, file immutability and a fresh-process reload. Its native
render and scene JSON are written under `out/scene`.

The existing LED example also observes its unchanged root hierarchy and checks
an empty delta so its tool-coverage gate includes the new inspection tool.
