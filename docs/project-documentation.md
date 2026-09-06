# Project-local hardware documentation

Once the hardware project directory is known, the AI should create this
structure inside that project using its normal file tools. KiCadFlow MCP does
not create or edit the documents, and existing documents must be preserved:

```text
docs/
  README.md
  design/
    design-spec.md
    hardware-implementation.md
  decisions/
    ADR-0000-template.md
  research/
    research-notes.md
    sources.json
  datasheets/
    README.md
  missing-information.md
```

`design-spec.md` owns requirements and design intent.
`hardware-implementation.md` owns schematic recommendations, PCB construction,
fabrication settings and verification. Datasheets and source provenance remain
beside those documents, not in global KiCad folders.

The MCP instructions require the caller to use primary sources, record exact
datasheet revisions and checksums, distinguish requirements from recommendations
and assumptions, and tell the user whenever evidence or design input is missing.
