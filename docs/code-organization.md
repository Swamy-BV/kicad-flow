# Code organization

Public primitives live in `schematic/api.py` and `pcb/api.py`, with their
data types alongside them. These packages do not import a backend or MCP.

## Backend responsibilities

`backend/kicad/schematic/sheet.py` and `backend/kicad/pcb/board.py` own the
document state, file lifecycle and transactions. Their typed methods delegate
to modules in the same directory:

| Responsibility | Schematic | PCB |
| --- | --- | --- |
| Library definitions and placed parts | `symbols.py`, `components.py` | `footprints.py` |
| Component fields | `fields.py` | `footprints.py` |
| Wires, labels and copper | `connections.py` | `copper.py` |
| Child sheets | `hierarchy.py` | — |
| Placement measurements | `placement.py` | `placement.py` |
| Electrical and layout checks | `validation.py` | `validation.py`, `connectivity.py` |
| Spatial observations | `scene.py` | `inspection.py` |
| Outlines and drawn graphics | `connections.py` | `graphics.py` |
| Construction and project rules | — | `settings.py`, `project.py` |

`_geometry.py` contains coordinate and intersection calculations. `_nodes.py`
contains format helpers. Feature modules accept the concrete document as an
explicit argument; the facade remains the place to find its public methods.
Type-only imports point back to the facade without creating import-time
cycles. Functions that construct temporary documents import it when called.

## MCP responsibilities

`server/board_tools/` and `server/schematic_tools/` organize tools by category.
Each has `models.py` for validated requests and `session.py` for open documents
and atomic list writes. The other modules contain the corresponding tools.

`tools_board.py` and `tools_schematic.py` register and re-export those tools,
preserving existing imports and tool lists. A new tool belongs in its category
and must be exported from the corresponding registration module. Shared code
must not import that registration module to reach session state or models.

## Keeping changes focused

Add code to the module that owns the responsibility. Split a module when its
responsibilities diverge; avoid arbitrary line limits and catch-all utilities.
Keep placement/routing choices with the caller and file mechanics in the backend.

Run lint, strict types and the FC/LED examples before committing. For changes
to observations, connector placement, search or execution, also run their
scripts in `examples/scripts/`. Inspect generated renders and report remaining
findings. Examples are fixed inputs, not fixtures to adjust around a regression.
