# MCP context delivery

KiCadFlow sends a short server instruction block at connection time. Detailed
schematic, PCB, parts and documentation workflows are fetched only when needed.
Individual CAD tools retain their own descriptions and schemas.

For substantial work, read only the relevant workflow and reuse it while the
task is unchanged. Simple tool lookups do not require a workflow read.

| Topic | MCP resource |
| --- | --- |
| Schematic | `kicad-flow://workflows/schematic` |
| PCB | `kicad-flow://workflows/pcb` |
| Parts | `kicad-flow://workflows/parts` |
| Documentation | `kicad-flow://workflows/documentation` |

Clients without resource access can call `get_workflow(topic="pcb")`, selecting
one of those four topics. Both interfaces serve the same source text. There is
no all-workflows operation. Engineering guidance remains in Circuit Context;
these workflows explain use of the CAD tools.

## Measured reduction

On 2026-09-20, before this change the local server combined its workflows into
24,482 characters of initialization instructions. The replacement contains
870 characters, a 96.4% reduction. The raw tool descriptions total 34,613
characters across 125 tools, including the new workflow reader.

The connected client in the review exposed the old shared instructions as a
prefix on every tool description, making its aggregate catalogue approximately
3.1 million characters. That was catalogue text, not measured active-context
usage. This change reduces the shared prefix at its source; it does not control
how a client loads, caches or displays tools, and is not a token-cost claim.

The five-item operation limit, caller-chosen geometry, staged transfer and CAD
verification requirements remain explicit in the short instructions. Workflow
bodies are not appended to tool descriptions or initialization instructions.

## Verification and activation

```powershell
python examples/scripts/workflow_context.py
python examples/scripts/batch_limits.py
python -m ruff check .
python -m mypy
```

The workflow check bounds initialization to 1,100 characters and total raw tool
descriptions to 40,000. It checks resource/tool text equality, valid topic
selection, the placement schema limit, legacy initialization and tool-search
dispatch. It fails if full workflows return to the always-loaded metadata.

Restart or reconnect an already running KiCadFlow server to load the new code
and metadata. Earlier descriptions and tool output already present in a
conversation are not retroactively removed.

When orchestrating calls, return one useful representation of a result rather
than printing both its text and structured forms. Filter large inventories and
logs before including them in model-visible output. The server does not remove
the standard structured result fields from existing CAD APIs.
