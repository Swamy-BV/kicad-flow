# Transfer a board in placement batches

`update_board_from_schematic` accepts a subset of the schematic's missing or
changed footprints. With the default five-item limit, a twelve-component board
can be transferred as five, five and two placements. Every coordinate remains
caller supplied. The operation does not increase or bypass the batch limit.

After each call, inspect:

- `placed`: references placed or replaced in this call.
- `changed_footprints`: existing footprints replaced in this call.
- `remaining_refs`: missing or changed footprints still awaiting placements.
- `complete`: whether all missing or changed footprints have been transferred.

Use references from `remaining_refs` in the next batch. Do not route or release
the design until transfer is complete and the normal checks pass. `complete`
describes footprint transfer, not electrical correctness or fabrication readiness.
Continue passing the same explicit `net_names` aliases when using aliases.

Each successful batch is saved and can be resumed after closing or reopening
the board. A refused batch leaves the board unchanged, including earlier
successful batches. Deferred footprints retain their existing geometry and
pad nets. Missing pads on footprints being synchronized still cause refusal.
Existing copper that would lose its pad contact blocks a replacement.

Duplicate, unknown and already-current references are refused. If a response
is lost, inspect the board before retrying; resubmitting an already completed
batch is refused rather than placing duplicates. An empty placement list is
allowed only when no missing or changed footprints remain.

The separate `sync_board_nets` tool remains strict: it requires all schematic
pads to exist. Partial synchronization is internal to staged transfer and is
not a public bypass for incomplete boards.

## Verification

Run at the default limit of five:

```powershell
python examples/scripts/staged_board_transfer.py
python examples/scripts/board_net_sync.py
python examples/scripts/batch_limits.py
python -m ruff check .
python -m mypy
```

On 2026-09-20 these checks passed, including 5/5/2 transfer, rejection of six
placements, resume from disk, actual pad-net membership, deferred replacements,
and rollback after missing-pad and copper-contact failures.

The existing `fc.py` and `led_digits.py` examples were also attempted in an
isolated output directory at limit five. They stop on existing oversized
requests: 14 power symbols and 32 schematic components respectively. Their
inputs were not changed and the limit was not raised. They do not constitute
passing end-to-end checks at this limit.

An already running MCP process must be restarted to load the updated Python
implementation and tool descriptions.
