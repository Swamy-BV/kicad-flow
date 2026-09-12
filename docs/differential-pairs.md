# Differential-pair observations

The caller selects both nets, routes them with `add_tracks`/`add_vias`, and
uses `measure_routes` to inspect the result. Pair membership, endpoints,
spacing and length limits are explicit; no route is chosen automatically.

```json
{
  "path": "board.kicad_pcb",
  "pairs": [{
    "first": "USB_DP", "second": "USB_DM",
    "first_start": {"ref": "J1", "pad": "3", "layer": "F.Cu"},
    "first_end": {"ref": "U1", "pad": "12", "layer": "F.Cu"},
    "second_start": {"ref": "J1", "pad": "2", "layer": "F.Cu"},
    "second_end": {"ref": "U1", "pad": "13", "layer": "F.Cu"},
    "gap_min": 0.15, "gap_max": 0.20,
    "max_uncoupled": 2.0, "max_skew": 0.5
  }]
}
```

These are illustrative dimensions and pad numbers, not USB design rules.
Choose limits from the design and stackup, configure netclasses and board
constraints, then read them back before routing.

`pairs[].inspection` reports:

- Each endpoint path's segments, transitions, lengths and topology issues.
  Detached tracks are listed but excluded from the path length.
- `path_skew`: absolute path-length difference, including traversed via
  barrels between copper centers when saved layer depths are available.
- Per-net spacing totals and fault spans with UUIDs, coordinates, layers,
  measured edge gaps and candidate partner IDs.
- `within_requested_limits`: false for a measured violation, null for an
  incomplete assessment without a definite violation, true when the
  supported geometry satisfies the supplied limits. `ok` means the tool ran.

Spacing compares parallel straight segments with overlapping projections on
the same layer. Multiple partners remain ambiguous; none is picked as nearest.
`max_uncoupled` bounds each path's combined unpaired and outside-gap length.
Ambiguous spans are reported separately. Omitted optional limits are not checked.

Repair the reported track UUIDs, remeasure, then run native `check_board` and
verify connectivity. This is a centerline observation: it does not reconstruct
pad-edge contacts, plated-pad transitions or plane paths. Arcs and zone paths
are unsupported; branches and loops are reported without choosing a path.
Via spacing, reference planes, impedance and propagation delay remain
unverified. A geometry pass is not electrical sign-off.

Legacy `{first, second}` calls still return total authored track lengths and
their difference as `skew`; that value is not an endpoint path measurement.
Replies refuse oversize results instead of truncating: at most eight pairs,
500 copper/pad objects per selected net, 1,000 fault spans per side, and the
tool's `max_bytes` limit (default 100,000).

Run `python examples/scripts/differential_pairs.py` for routed MCP regressions
and renders in `out/differential-pairs`. It exercises gap and path-skew faults,
45-degree bends, zone refusal, disconnections, branches, ambiguous partners,
layer changes, missing stackup, schema/reply limits and repair. It tests tool
behavior, not autonomous AI routing.
