# Manufacturing exports

The active fabrication provider owns a dated output-requirements contract.
`get_manufacturing_requirements(path, service, assembly_sides)` returns the
required Gerber layers, drill settings, BOM/CPL columns, source URLs and dates.
The caller selects `pcb` or `pcba`; PCBA requires explicit `front`, `back`, or
both assembly sides. There is no implicit provider or assembly-side selection.

## Primitive workflow

1. `set_fabrication_profile` selects the provider and process choices.
2. `get_manufacturing_requirements` reads the resulting output requirements.
3. `export_gerbers(path, output_dir, service, assembly_sides)` plots all required
   copper, mask, silkscreen and outline layers, plus paste on selected PCBA sides.
4. `export_drills(path, output_dir, include_map=True)` exports separate plated
   and non-plated Excellon files, optionally accompanied by drill maps.
5. For assembly, `export_bom(path, schematic_path, output_file, assembly_sides,
   part_number_field="LCSC")` exports hierarchical schematic values and supplier
   identifiers. `export_placements(path, output_file, assembly_sides)` exports
   actual native footprint positions and rotations.
6. `check_manufacturing_package(path, service, files, assembly_sides,
   schematic_path, bom_file, placements_file)` checks explicit outputs against
   fresh native exports and returns issues, DRC findings and file hashes.
7. `archive_manufacturing_files(files, output_zip)` archives exactly the supplied
   files. For JLCPCB, archive CAM outputs and supply BOM/CPL separately when
   ordering assembly. Archiving does not imply a passing package check.

Every output destination must be new. Failed exports cannot reuse old files.
Gerbers/drills are published as complete directories; CSV and ZIP outputs are
published after successful generation. No operation uploads or orders anything.
Native plotting checks zone fills on a snapshot. Package DRC uses the existing
isolated proposed-board check without modifying the editable board.

## JLCPCB output contract

The contract is in `providers/jlcpcb/capabilities.json`, with source provenance.
It specifies RS-274X plots, Protel extensions, mask subtraction from silkscreen,
Excellon drills in millimetres, absolute origin and separate PTH/NPTH outputs.
Drill maps are **recommended**, not required. Empty drill content is legitimate
when there are no corresponding holes; the files must still have native headers.

BOM columns: `Comment`, `Designator`, `Footprint`, `LCSC Part #`.
CPL columns: `Designator`, `Mid X`, `Mid Y`, `Layer`, `Rotation`.
Columns and side spellings are mapped by the provider; the CAD contract returns
neutral rows. The caller explicitly names the schematic supplier-number field.
No stock, substitutes, supplier orientation corrections or part numbers are
inferred. BOMs are ungrouped, one reference per row. Native DNP and exclusion
flags are honored; differing BOM/CPL inclusion is reported as a mismatch.
References on unselected assembly sides are omitted from the BOM.
Save child sheets first if autosave has been disabled: the native hierarchy
export reads children from disk, as do existing netlist and render operations.

## What validation proves

The check detects missing, altered or stale CAM records by comparing them with
fresh native output, ignoring only KiCad timestamp comment lines. It compares
BOM fields against the schematic and CPL records against the board, rejects
malformed/duplicate CSV designators, reports missing supplier identifiers and
BOM/CPL reference mismatches, and returns native DRC findings. Missing supplier
identifiers mean manual part selection remains unresolved; the supplier may
accept the BOM, but this tool does not label it release-ready.

`ok` means the check ran. `passed` means no reported issues or DRC findings.
This is not an independent Gerber viewer, a BOM stock check, an electrical
schematic/PCB parity certification, or an assembly/impedance certification.
Supplier part orientations and CAM previews still need inspection. Timestamp
normalization is confined to the KiCad backend; unrelated content changes fail.

Run `python examples/scripts/manufacturing.py` for MCP integration checks. It
copies the LED fixture into `out/manufacturing`, exports its files and exercises
refusals and package diagnostics. Its missing supplier numbers and DRC findings
remain visible; an example passing does not certify that fixture for production.
