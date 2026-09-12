# Local observations

Inspect the area being edited, keep its returned revision, then request only
changes. Coordinates and obstacles come from the server; placement and routing
remain caller decisions. The dashboard does not stream these tool payloads.

## Schematics

Use `inspect_schematic_scene` with an explicit rectangle and `detail="compact"`.
This retains all selected geometry, flattens properties, and uses `[x,y]` pairs
for anchors and points. Pin anchors remain exact; text bounds are estimated.

For overlap repair, `detail="conflicts"` returns findings and only the objects
they implicate. It omits other obstacles: use compact or full before planning
new wires. Kind counts summarize the whole region in every mode.

Pass `since` with the same rectangle and detail. Apply changed objects and
findings by ID, and delete returned removal IDs from the local observation.
Changing scope or using an expired revision returns a full reset.

## PCB routing

`query_board_region` accepts a rectangle, explicit layers, `since`,
`max_objects` and `max_bytes`. Start with the local area and the layers you
intend to inspect. All nearby nets remain visible as potential obstacles.

- Pads report shape, rotation, size, corner ratio and copper layers.
- Tracks, vias, zones and pads have stable observation IDs.
- Nearby board edges remain visible when filtering copper layers.
- Back-side coordinates use the same board axes as front-side coordinates.
- `include_fills=true` includes stored zone-fill contours. Refill zones after
  copper edits before relying on them; boundary polygons alone are not copper.

A delta returns changed objects in their normal collections and a `removed`
map of IDs per collection. `mode="full"` means replace the local observation.
An oversized reply is refused with guidance to narrow the query; obstacles are
never silently truncated. Both object and byte limits apply to schematic views
as well. Cache storage is bounded across clients.

Bounds are conservative and are not clearance envelopes. Custom pads,
chamfered/offset pads, pad stacks, copper arcs and multilayer zones have geometry
limitations explicitly reported. Unsupported pad geometry is included even
when its anchor bounds lie outside the requested rectangle. Copper graphics
and footprint copper/edges are also flagged as unsupported. These flags can
describe features anywhere on the board, beyond the selected rectangle.
Use candidate DRC to decide whether a proposed route is legal.

## Net properties before routing

1. Read the schematic's `list_nets` and apply membership with `set_pad_nets`.
2. Set manufacturing limits and choose routing classes deliberately using
   `set_net_classes` and `assign_net_classes`.
3. Read `list_board_nets(net="SIGNAL", include_rules=true)`. It reports effective
   class dimensions, their source classes, unassigned-pad count, and nets
   lacking explicit class assignments. Pattern assignments may also apply.
4. Use `set_board_constraints` for enforced conditional limits. Preferred class
   widths are not automatically hard DRC limits. Default is a valid class;
   review its effective values instead of guessing.
5. Inspect a local region, propose tracks/vias, check them with `check_board`,
   apply the checked geometry, then verify `unrouted_connections`.

KiCad resolves class inheritance and patterns. These observations do not
calculate impedance, infer differential pairs from names, choose a route, or
prove electrical connectivity from geometric overlap.

Run `python examples/scripts/local_observations.py` to exercise focused repair,
back-side geometry, inherited rules, reply limits and revision updates via MCP.
This checks tool behavior; it does not measure an AI's routing success rate.
