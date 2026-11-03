# KiCad 11 schematic IPC backend

The `Sheet` contract, MCP tool signatures, coordinates and typed batch models
are unchanged. The native document is authoritative. Each outer operation
reads a fresh IPC snapshot; a successful batch applies native item changes in
a commit. A refused native item drops the batch. Native statuses are checked
individually because an overall successful response can contain refused items.

Every attached write edits native objects directly: symbols, fields, wires,
labels, junctions, no-connects, text, graphics and child-sheet symbols. Typed
native objects are retained until the batch commits, because native placement
does not make staged objects queryable yet. There is no serialized edit diff
or file conversion codec. KiCad alone saves the actual project documents.

Library placement obtains native definitions in a private unsaved editor and
passes native objects directly into the real sheet's transaction. That editor
never saves a library schematic. Read-only scene and layout helpers still parse
native snapshots; detached candidate measurements and export snapshots remain
isolated from the authoritative document. Library queries open no document.

New child sheets use resolved filenames during native creation. Before saving,
the backend restores the caller's relative filename through IPC while retaining
the already-bound native child screen. Saved projects remain portable. Native
page-number allocation includes every sheet staged in a batch. Filename
canonicalization currently adds a separate native undo entry at save time.

Nets come from native `GetSchematicNetlist`, with pin identities resolved in
their sheet instance. Queries on a child include its descendants. CLI ERC and
rendering operate on private snapshots of the current unsaved hierarchy; a
child snapshot is explicitly rebased as a standalone root for those exports.

`KICAD_FLOW_SCHEMATIC_MODE` selects `headless` (default) or `gui`; there is no
automatic fallback between them. `gui` requires the requested root to be open
in a KiCad editor with IPC enabled. The SDK also honors `KICAD_API_SOCKET` and
`KICAD_API_TOKEN` for an explicitly selected editor connection.

`KICAD_FLOW_KICAD_CLI` selects the KiCad 11 executable. The SDK runs in
`KICAD_FLOW_IPC_PYTHON`, or the MCP interpreter if unset. The bootstrap script
builds a checksum-verified, pinned upstream development SDK in an isolated
environment because the released 0.8 SDK predates schematic support. `doctor`
checks both the selected KiCad version and that worker's SDK imports.

The worker closes its owned documents through native IPC before stopping its
headless server, releasing KiCad's project locks. GUI connections only
disconnect. Headless sessions refuse an already-locked project. Writes are
never replayed after a timeout. External file changes are checked before native
save, and native edits made during a batch cause it to refuse stale state.

Junctions are computed by KiCad: a free junction disappears when the native
commit finishes. Creation therefore refuses points with fewer than three
incident wire branches/pins. Crossing-junction creation/removal works. The old
LED and scene examples' free-junction assertions cannot pass with that behavior.
Native labels use quarter-turn spin styles; unsupported label angles are
refused. Mandatory symbol fields cannot be removed; their values can be set.

This nightly's sheet-creation IPC creates a fresh child screen even when the
named file already exists. Such attachments are refused before applying the
batch to protect that file. Opening and editing an existing hierarchy works;
attaching an existing child file and creating additional instances of that file
still need native attachment support. PCB primitives now use direct native
objects; see the [PCB IPC notes](../pcb/README.md). Windows CI now selects the
tested KiCad 11 nightly and pinned official development bindings. Full examples
still expose the documented native capability gaps; they are not skipped or
reported as passing.

Run `python examples/scripts/schematic_ipc.py` to check unsaved nested edits,
pin connectivity, rollback, a fresh reopen and project relocation.
`schematic_native.py` checks native transforms, multi-unit symbols and CRUD.
The existing `fc.py`, `led_digits.py`, and `schematic_groups.py` remain unchanged
integration inputs.

Upstream references: [Python schematic bindings](https://docs.kicad.org/kicad-python/schematic.html)
and [the tested native sheet-creation handler](https://gitlab.com/kicad/code/kicad/-/blob/ff9515f1e9/eeschema/api/api_handler_sch.cpp#L1355).
