# kicad-flow

An MCP server for authoring KiCad 10 schematics and printed circuit boards.
It exposes small, composable operations—place a part, inspect its pins, draw a
wire, assign a net, add copper—and reports the geometry or connectivity KiCad
actually produced.

The boundary is intentional: the caller chooses the circuit, placement and
routing; KiCadFlow handles file-format mechanics, readback and validation. It
does not contain an autoplacer, autorouter or inferred design policy.

## Examples

The scripts in [`examples/scripts/`](examples/scripts/) build these designs
through MCP and check the results.

| | |
| --- | --- |
| **[fc](examples/fc/)** — Multi-sheet STM32 flight-controller schematic exercise. It reports ERC and visual-layout findings separately.<br><br>[![](examples/fc/fc-3.png)](examples/fc/) | **[led_digits](examples/led_digits/)** — Full schematic/PCB and API-coverage fixture with completed routing.<br><br>[![](examples/led_digits/led_digits-3d.png)](examples/led_digits/) |
| **[art_board](examples/art_board/)** — Line-and-arc outline, cutouts and editable front/back silkscreen primitives.<br><br>[![](examples/art_board/art_board-3d.png)](examples/art_board/) | **[batman](examples/batman/)** — Shaped LED schematic and PCB with copper pours and reverse-side artwork.<br><br>[![](examples/batman/batman-3d.png)](examples/batman/) |
| **[esc4in1](examples/esc4in1/)** — AM32 four-channel hierarchy and dense two-sided placement exercise. The schematic is clean; the PCB remains an intentionally reported work in progress, not a fabrication reference.<br><br>[![](examples/esc4in1/esc4in1-3d.png)](examples/esc4in1/) | |

Run an example from the repository root, for example:

```powershell
.\.venv\Scripts\python.exe examples\scripts\led_digits.py
```

Tested on Windows with KiCad 10. Other platforms are not yet supported.
Coordinates and dimensions are in millimetres.

## Quick start

Requirements:

- Python 3.10 or newer
- KiCad 10, with `kicad-cli` on `PATH` or in its default Windows installation

From PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m kicad_flow.server doctor
```

`doctor` checks the installation without changing files.

## Run the server

| Mode | Command | Connect from the MCP client |
| --- | --- | --- |
| stdio | `.\.venv\Scripts\python.exe -m kicad_flow.server` | Configure the client to spawn this command. |
| HTTP | `.\.venv\Scripts\python.exe -m kicad_flow.server --http` | `http://127.0.0.1:8471/mcp` |

Use stdio for desktop clients. Run with `--help` for server options.
Add `--tool-search` to discover tool schemas on demand. See
[execution and discovery](docs/mcp-execution.md) for behavior and benchmarks.

The live monitor at `http://127.0.0.1:8472` shows the active design and tool calls.
Board 3D view includes top, bottom and edge cameras; the bottom views expose
back-side placement and routing during a build.
Set `KICAD_FLOW_MONITOR=0` to disable it, or use another port number.

## What the server covers

- **Schematics:** components, wiring, labels, hierarchical sheets, netlists,
  ERC and layout checks.
- **PCBs:** footprints, tracks, vias, zones, stackups, outlines, silkscreen,
  DRC and 2D/3D renders.
- **Preflight:** check proposed component placement or board routing before
  applying it. List writes are atomic.
- **Spatial inspection:** query schematic geometry and changes without images.
  See the [scene API guide](docs/schematic-scenes.md).

The caller supplies placement and routing decisions. A successful tool call
does not mean the design passes ERC or DRC.

## Providers and project assets

Optional JLCPCB support includes manufacturing capabilities and a local parts
catalogue for `search_parts`. Data is a downloaded snapshot; verify stock and
pricing before ordering. See [provider setup](src/kicad_flow/providers/jlcpcb/README.md).

Import symbols, footprints and 3D models into project-local libraries without
changing KiCad's global libraries. For design notes and datasheets, see
[project documentation guidance](docs/project-documentation.md).

## Windows release

Build a standalone application:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

Run `dist\kicad-flow\kicad-flow.exe`. Distribute the entire directory.
KiCad 10 is still required; the optional parts catalogue is separate.

## Claude Desktop example

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

## Development

```powershell
python -m ruff check .
python -m mypy
python examples\scripts\fc.py
python examples\scripts\led_digits.py
python examples\scripts\symbol_search.py
```

Examples are the integration checks; there is no separate unit suite.
