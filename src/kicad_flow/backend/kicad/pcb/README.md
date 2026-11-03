# KiCad 11 PCB IPC backend

The `Board` contract and MCP primitive signatures are unchanged. Placement,
movement, rotation, flipping, fields, pad-net transfer, tracks, vias, zones,
graphics, text and deletions construct or modify official native objects through
IPC. Native KiCad saves the actual board. There is no S-expression edit diff,
temporary file codec, SWIG runner or KiCad 10 fallback on this editing path.

Plural writes stage typed native objects, validate per-item IPC statuses, and
push one native commit. A refusal drops that commit. The source board file is
revision-checked; matching saves made by the attached GUI are accepted. Reload
closes the previous native document before reopening it.
Existing footprint fields are updated as native children, avoiding a geometry
round trip of the whole footprint. Before committing, the adapter checks their
angles against a native save snapshot; affected nightlies can echo the requested
angle while saving a different one. A mismatch rolls back the whole batch.

Library placement uses a private unsaved native board to obtain KiCad's actual
front/back library geometry. It passes native objects directly to the actual
board's transaction. This is necessary because `PlaceFootprintFromLibrary`
cannot take Reference/Value, and `UpdateItems` cannot address a newly staged
placement. The library document is never serialized or saved.

Headless schematic and PCB clients share a native project host, rather than
competing for its lock. GUI connections attach only to the explicitly requested
open board. Select `KICAD_FLOW_PCB_MODE=headless` (default) or `gui`, using the
same CLI, SDK Python, socket and token configuration as the schematic adapter.
An external project's lock is never bypassed.
Library-only observations open no document and work in either mode.

Connectivity, text bounds, manufacturing limits, netclasses, assignments and
custom rules use native IPC. Existing read-only geometry helpers still parse
native document snapshots. Candidate checks and export snapshots use native
SaveCopyOfDocument; CLI DRC, rendering and manufacturing export remain observations.
Specctra design export uses the native CLI export command.
Session import uses native `ImportSpecctraSession` in a private headless board,
rejects skipped items, verifies outline, placements, nets and layers, then
publishes a separate board with its manufacturing profile and project files.
Snapshot observations understand KiCad 11's footprint `transform` blocks.

Current limitations:

- This nightly declares `UpdateBoardStackup` unimplemented. Physical stackup
  writes and creating a board with a non-default thickness are refused.
- Effective netclasses are observed natively, but per-field ownership is not
  exposed. Policy replies report unknown owners and an explicit warning.
- Layer-filtered custom rules are refused: this nightly writes their layer
  condition but omits it from IPC readback, preventing verification.
- Non-orthogonal footprint protobuf round trips can produce native
  `lib_footprint_mismatch` warnings. A direct SDK probe reproduced this after
  both cloning and a whole-footprint field update; initial native placement
  did not produce that warning. The existing transform audit remains failing
  for those cases. Quarter-turn front/back checks pass.
- Native field updates can ignore absolute text angles. These requests are
  refused when the native snapshot disagrees, including value-only changes
  that would alter an existing field's angle. Moving a field at a supported
  angle preserves footprint geometry.
- The contour helper supports curve sampling and convex insets; concave
  insets are refused instead of returning an incorrect offset polygon.
- Windows CI selects the tested KiCad 11 nightly and pinned development SDK;
  the full example checks still expose the native gaps above.

The SDK bootstrap generates the protocol from the native build's exact commit.
Its pinned Python wrapper needs one build-time adaptation: the removed
`DrillChartAlign` enum is replaced with the native `HorizontalAlignment` import
and annotations. No installed SDK or native process is patched at runtime.

Run `python examples/scripts/pcb_ipc.py` for the MCP-only integration check.
It covers SMD/THT footprints on both faces at four rotations, schematic-owned
nets, rotate/flip round trips, text, graphics, copper, rollback, settings, native
reload and top/bottom renders. Native quarter turns retain integer coordinates
to avoid the SDK's floating-point truncation drift.
`python examples/scripts/routing_ipc.py` imports a recorded FreeRouting session
on a rounded board with a circular cutout. Malformed sessions, unresolved
references and changed placements are refused without publishing an output.
The valid import has zero unrouted connections, DRC errors and DRC warnings.
`python examples/scripts/pcb_fields_ipc.py` verifies field edits on a recorded
native 37-degree footprint, batch rollback, angle refusal and reload with zero
library-mismatch findings.

The full LED example is not yet passing: stackup and layer-filtered rule checks
are unavailable, and its free-junction expectation conflicts with native
schematic cleanup. Unsupported free junctions are refused explicitly. Its routed
PCB reached zero unrouted connections and zero DRC errors; warnings remain.
