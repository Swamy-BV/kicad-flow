# FreeRouting exchange

KiCadFlow keeps manual `add_tracks` and `add_vias` as its primitive routing
API. These three MCP calls provide an explicit exchange with the external
FreeRouting program:

1. `export_routing_design(path, output_file)` writes a Specctra `.dsn` from the
   current open board. The board needs a closed `Edge.Cuts` outline.
2. `run_freerouting(dsn_path, ses_path, jar_path, java_path="java")` runs a
   caller-installed FreeRouting JAR in headless mode and writes a `.ses`.
   `max_passes` and `timeout_seconds` are optional. Neither board file changes.
3. `import_routing_session(path, session_file, output_file)` applies the session
   to a **new** `.kicad_pcb` beside the source. It preserves the source board,
   checks footprint placement, pad nets, layer count and outline, and returns
   track, via, unrouted and DRC counts. It also copies same-project schematic
   and rule files to the output stem when present.

Download the JAR from [FreeRouting releases](https://github.com/freerouting/freerouting/releases)
and install Java separately; neither is bundled or downloaded by the MCP server.
The JAR path is supplied for each run. Run
`check_board` on the returned board and inspect its copper before adopting it.
FreeRouting may choose angles, vias or a route that differs from the caller's
style. Use `check_board(track_angle_step=45)` if 45-degree geometry is required.
The import operation reports DRC findings rather than treating a connected
ratsnest as proof that the PCB is fabrication ready.

The standalone MCP example exercises DSN export without FreeRouting installed:

```powershell
python examples/scripts/freerouting_exchange.py
```

Set `FREEROUTING_JAR` to a local JAR and optionally `FREEROUTING_JAVA` to a Java
executable before running it to exercise the full DSN-to-SES-to-PCB round trip.
It asserts that the original board still has its unrouted connection and that
the imported board has none and passes DRC.
