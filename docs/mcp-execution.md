# Execution and tool discovery

The primitive API stays the same: callers choose coordinates and routes.
Place components, read the returned pins, then wire in a separate request.

Calls touching the same project directory wait for each other, including
reads and autosave. Independent project directories can run concurrently.
Hierarchy checks, netlists and schematic renders hold a server-wide design
gate because child sheets can live outside the root directory. These gates
coordinate one server process; external editors and other servers are separate.

Cancelling a waiting request prevents its execution. A synchronous operation
already running finishes while retaining its gate. Cancellation does not undo
changes. A stuck native operation can therefore keep its project waiting.

`batch` dispatches each primitive through FastMCP's validation and middleware.
Successful writes autosave even when a later operation fails. Activity records
show each primitive once. A batch is not a transaction: earlier successful
operations remain applied. Individual typed list writes retain their rollback
behavior.

Clients requesting progress receive operation counts for batches and start/end
notifications for checks and renders. Completion means execution finished;
inspect the returned findings to determine whether the design passed.

## Optional discovery

Start either transport with `--tool-search`, for example:

```powershell
python -m kicad_flow.server --http --tool-search
```

The initial catalogue exposes `search_tools` and `call_tool`. Search matches
names, descriptions and parameters using a case-insensitive regular expression,
returning at most eight full schemas. For example, search `^add_components `,
then call `call_tool` with `name="add_components"` and its normal `arguments`.
Known primitives remain directly callable. The default catalogue is unchanged.

## Reproduce the checks

```powershell
python examples/scripts/mcp_execution.py
```

This exercises concurrent workers, cancellation, validation, partial-batch
autosave, logging and progress through MCP. It then builds the same small
circuit using direct calls and discovery, comparing geometry counts and native
connectivity. Results are written to `out/mcp-execution/benchmark.json`.

The benchmark counts JSON catalogue, request arguments and result payloads;
it excludes protocol envelopes and progress messages. It searches before each
primitive, so caching discovered schemas would reduce later discovery calls.
In-process timings include warm-up effects and are not a model speed comparison.
This does not measure model tokens, decision quality or schematic neatness.
