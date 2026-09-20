<h1 align="center">KiCadFlow</h1>

<p align="center"><strong>Build, inspect, and validate KiCad designs through MCP.</strong></p>

<p align="center">
  Small composable tools for schematics, PCB layout, manufacturing constraints,<br>
  and visual review—backed by KiCad's own file formats and command-line checks.
</p>

<p align="center">
  <a href="https://github.com/Swamy-BV/kicad-flow/actions/workflows/ci.yml"><img src="https://github.com/Swamy-BV/kicad-flow/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <a href="https://github.com/Swamy-BV/kicad-flow/actions/workflows/codeql.yml"><img src="https://github.com/Swamy-BV/kicad-flow/actions/workflows/codeql.yml/badge.svg" alt="CodeQL status"></a>
  <a href="https://github.com/Swamy-BV/kicad-flow/actions/workflows/scorecard.yml"><img src="https://github.com/Swamy-BV/kicad-flow/actions/workflows/scorecard.yml/badge.svg" alt="OpenSSF Scorecard status"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.10 or newer"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-blue.svg" alt="AGPL 3.0 license"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#capabilities">Capabilities</a> ·
  <a href="#examples">Examples</a> ·
  <a href="#documentation">Documentation</a> ·
  <a href="#development">Development</a>
</p>

<p align="center">
  <a href="examples/led_digits/"><img src="examples/led_digits/led_digits-3d.png" alt="A four-digit LED display PCB generated through KiCadFlow" width="760"></a><br>
  <sub>A routed four-digit display created and checked end to end through MCP.</sub>
</p>

KiCadFlow is an MCP server for authoring KiCad 10 schematics and printed
circuit boards. An AI agent or other MCP client can place components, inspect
their actual pins, draw wires, synchronize a schematic with a PCB, route
copper, run ERC and DRC, and render the result for review.

The caller chooses the circuit, placement, and routing. KiCadFlow handles the
file-format mechanics, reports the geometry and connectivity KiCad produced,
and makes validation results available as structured data. This keeps the API
predictable: each primitive does one job and returns what it made.

## Quick start

### Requirements

- Windows
- Python 3.10 or newer
- KiCad 10, with `kicad-cli` on `PATH` or in its default installation folder

Install from a clone and verify the environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m kicad_flow.server doctor
```

Start the server over stdio for a desktop MCP client:

```powershell
.\.venv\Scripts\python.exe -m kicad_flow.server
```

Or expose it over HTTP:

```powershell
.\.venv\Scripts\python.exe -m kicad_flow.server --http
```

The MCP endpoint is `http://127.0.0.1:8471/mcp`. The live monitor opens at
`http://127.0.0.1:8472` and shows the active design, renders, and tool calls.
Run with `--help` for all server options.

<details>
<summary><strong>Claude Desktop configuration</strong></summary>

Use stdio and point `command` at the virtual-environment interpreter:

```json
{
  "mcpServers": {
    "kicad-flow": {
      "command": "C:\\path\\to\\kicad-flow\\.venv\\Scripts\\python.exe",
      "args": ["-m", "kicad_flow.server"]
    }
  }
}
```

Restart Claude Desktop after changing its configuration.

</details>

## Capabilities

| Area | What the MCP tools provide |
| --- | --- |
| **Schematics** | Components, fields, wiring, labels, visual group boundaries, hierarchical sheets, footprint assignment, netlists, ERC, and layout checks |
| **PCB layout** | Schematic-to-PCB export and synchronization, footprint movement, tracks, vias, zones, outlines, stackups, silkscreen, and editable board text |
| **Inspection** | Pin and pad geometry, connectivity, local spatial observations, revision deltas, text bounds, top/bottom renders, and 3D views |
| **Validation** | Atomic preflight checks for proposed writes, ERC, DRC, schematic/PCB parity checks, and manufacturing limits |
| **Manufacturing** | Fabrication profiles, JLCPCB capabilities and local parts data, Gerber/drill export, JLCPCB BOM/CPL, package checks, ZIP creation, and project-local library assets |
| **Routing exchange** | Explicit DSN export and SES import for optional FreeRouting workflows while preserving KiCadFlow project metadata |

All caller-supplied coordinates and dimensions are in millimetres. A successful
write means the operation completed; the design is complete only when its
connectivity, ERC, DRC, and rendered output have also been checked.

## Examples

Every example is built through `Client(mcp)`, so the scripts exercise the same
tool schemas and contracts exposed to an MCP client.

<table>
  <tr>
    <td width="50%" align="center">
      <a href="examples/led_digits/"><img src="examples/led_digits/led_digits-3d.png" alt="Four-digit LED display PCB" width="100%"></a><br>
      <strong><a href="examples/led_digits/">LED digits</a></strong><br>
      Complete schematic, PCB, routing, and API-coverage fixture.
    </td>
    <td width="50%" align="center">
      <a href="examples/batman/"><img src="examples/batman/batman-3d.png" alt="Batman-shaped LED PCB" width="100%"></a><br>
      <strong><a href="examples/batman/">Batman</a></strong><br>
      Shaped LED board with copper pours and reverse-side artwork.
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <a href="examples/art_board/"><img src="examples/art_board/art_board-3d.png" alt="Mooncat purple PCB art with a crescent and stars" width="100%"></a><br>
      <strong><a href="examples/art_board/">Mooncat art board</a></strong><br>
      Celestial cat artwork, purple solder mask, and an orbital fish on the reverse.
    </td>
    <td width="50%" align="center">
      <a href="examples/esc4in1/"><img src="examples/esc4in1/esc4in1-3d.png" alt="Four-in-one ESC PCB" width="100%"></a><br>
      <strong><a href="examples/esc4in1/">4-in-1 ESC</a></strong><br>
      AM32 hierarchy and dense two-sided placement exercise. The PCB remains a reported work in progress, not a fabrication reference.
    </td>
  </tr>
</table>

The [flight-controller example](examples/fc/) exercises a multi-sheet STM32
schematic and reports electrical and visual-layout findings separately.

Run an example from the repository root:

```powershell
.\.venv\Scripts\python.exe examples\scripts\led_digits.py
```

## Design model

KiCadFlow separates facts from design decisions. The server resolves facts a
caller cannot safely infer from the file format, such as the position of a
rotated and mirrored pin, the net KiCad assigned to a pad, or the exact object
created by a write. The caller supplies choices such as component placement,
wire paths, labels, and routing.

Repeatable writes accept lists and apply atomically. This lets an agent submit
a placement or routing stage, inspect it on an isolated board, and commit it
without hiding decisions inside an autoplacer or autorouter. The optional
[FreeRouting exchange](docs/freerouting.md) remains an explicit external step.

## Documentation

- [MCP execution and on-demand tool discovery](docs/mcp-execution.md)
- [Local schematic and PCB observations](docs/local-observations.md)
- [Routing preview and fabrication-profile validation](docs/routing-preview-validation.md)
- [Differential-pair inspection and validation](docs/differential-pairs.md)
- [Manufacturing requirements and exports](docs/manufacturing.md)
- [PCB text editing and native bounds](docs/pcb-text.md)
- [Net colors and class assignments](docs/net-colors.md)
- [Project documentation and datasheet guidance](docs/project-documentation.md)
- [Code organization](docs/code-organization.md)

Optional JLCPCB support uses a downloaded manufacturing-capability and parts
snapshot. Verify current stock, pricing, and capabilities before ordering. See
the [provider setup guide](src/kicad_flow/providers/jlcpcb/README.md).

## Batch size

MCP operation lists accept at most **5 items per call** by default. This covers
schematic and PCB additions, moves, updates, removals and candidate checks, plus
at most 5 operations in `batch`. Each enclosed operation keeps the same limit.
Oversized lists are rejected before edits; `batch` checks every enclosed list
before running its first operation, even with `stop_on_error=false`.

Set `KICAD_FLOW_BATCH_LIMIT` to a positive integer **before starting the server**
and restart it to experiment with another size. The advertised `maxItems`
schemas and server instructions use that value. Invalid values stop startup.
For example, in PowerShell:

```powershell
$env:KICAD_FLOW_BATCH_LIMIT = "10"
python -m kicad_flow.server --http
```

Split additive edits into separate calls and inspect their replies. Whole-list
replacement tools, such as `set_net_class_patterns`, still replace the entire
collection: increase the configured limit for a larger replacement instead of
splitting it. Polygon vertices, stackup layers, filter layers, file manifests
and returned inventories are not operation batches. The underlying primitive
contracts do not impose this MCP request-size policy.

The existing large examples keep their original inputs. Run them with a larger
explicit limit, for example `KICAD_FLOW_BATCH_LIMIT=1000`; use
`python examples/scripts/batch_limits.py` to exercise the configured boundary.

## Tool discovery and monitoring

Add `--tool-search` to either transport to expose a compact discovery surface
instead of the full tool catalogue at startup. See the
[execution guide](docs/mcp-execution.md) for behavior and measured context-size
results.

The live monitor has separate Schematic and PCB tabs, with a document selector
for design files in the active project folder. PCB views include 2D top/bottom
and 3D top, bottom, and edge cameras. Fit full view is the default; zoom presets
and activity-panel visibility are remembered in your browser.

Local path-like strings are masked in browser activity. Local activity and
`replay.jsonl` files retain their original data. Expand **Interaction statistics**
for completed calls, failures, retries, timings and structured JSON sizes across
the latest 500 records. These sizes exclude images and protocol overhead; the
MCP server cannot observe model context usage or billed tokens. Set `KICAD_FLOW_MONITOR=0` to disable the monitor or set it to a
different port number.

## Windows release

Build the standalone application:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

Run `dist\kicad-flow\kicad-flow.exe` and distribute the entire directory.
KiCad 10 remains required; the optional parts catalogue is separate.

## Development

```powershell
python -m ruff check .
python -m mypy
python examples\scripts\fc.py
python examples\scripts\led_digits.py
python examples\scripts\symbol_search.py
```

The examples are the integration checks; there is no separate unit suite. See
[code organization](docs/code-organization.md) for module responsibilities.
