# Routing preview and fabrication thickness

`check_board(path=..., tracks=[])` must report the same findings as checking
the current board. The candidate board now receives sibling `.kicad_pro` and
`.kicad_dru` copies before zone filling and DRC. Previously only the board tree
was copied, causing KiCad to use default netclasses and manufacturing limits.
The temporary files remain beside the original board to preserve relative
project paths and are removed in the existing `finally` cleanup.

`set_stackup` continues to record the physical copper/dielectric sum. The
fabrication profile records nominal ordering thickness and the provider's
absolute thickness tolerance separately. `set_fabrication_profile` accepts
an explicit `nominal_thickness` when the physical sum is not an orderable value;
omission preserves the old exact-choice behavior. Out-of-tolerance boards are
rejected. Old sidecars retain strict checking until explicitly re-resolved.

Run `python examples/scripts/routing_preview.py` to exercise these contracts
through `Client(mcp)` and native KiCad. It checks default/project rules, custom
rules, filled-zone previews, real short attribution, source-file hashes,
temporary-file cleanup, nominal thickness boundaries, and legacy profile
refresh. Generated fixtures go under `out/routing-preview/`.
