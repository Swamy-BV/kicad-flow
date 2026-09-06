"""Instructions for the project-local engineering record."""

DOCUMENTATION = """

PROJECT DOCUMENTATION AND RESEARCH

As soon as the hardware project directory and name are known, create the
project-local documentation structure described below using your normal file
tools. This MCP server does not create or edit documentation. Every design
document, research note, decision record, source manifest and downloaded
datasheet belongs below that project's `docs/` directory. Never place project
evidence in a global KiCad, provider, home, or temporary directory.

Create `docs/design/design-spec.md`,
`docs/design/hardware-implementation.md`, `docs/decisions/`,
`docs/research/research-notes.md`, `docs/research/sources.json`,
`docs/datasheets/`, and `docs/missing-information.md`. Preserve existing files;
fill missing structure without replacing prior engineering work.

Research before selecting a component, topology, protection value, stackup or
fabrication rule that depends on external facts. Prefer current primary sources:
the component manufacturer's datasheet and application notes, the fabricator's
capability page, and the applicable standards body. Save the exact manufacturer
PDF in `docs/datasheets/`, name it by manufacturer/part/revision, and record its
URL, revision, retrieval date, local path and SHA-256 in
`docs/research/sources.json`. Secondary distributors and community libraries
may help discovery but do not replace the manufacturer's electrical truth.

Maintain `docs/design/design-spec.md` for requirements, architecture, budgets,
calculations, assumptions, risks and acceptance criteria. Maintain
`docs/design/hardware-implementation.md` for schematic recommendations, PCB
construction, stackup, fabrication profile, routing rules and verification.
Use an ADR in `docs/decisions/` when two reasonable implementations exist;
record the alternatives and why one was chosen.

Never convert a recommendation into a requirement or an assumption into a
fact. Cite source IDs beside consequential values. When evidence is missing,
unavailable, stale or contradictory, update `docs/missing-information.md` and
tell the user plainly: what is missing, what was checked, what it affects, and
what input or document would resolve it. Do not hide missing information behind
a plausible default. Update the documentation when the design changes, not only
after the board is finished.
"""

__all__ = ["DOCUMENTATION"]
