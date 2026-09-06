"""Instructions for selecting provider parts and local CAD assets."""

PARTS = """

PART PROVIDERS AND PROJECT LIBRARIES

Provider selection is explicit. When the user specifies JLCPCB assembly, call
`get_parts_provider_status(provider="jlcpcb")`, then use `search_parts` before
choosing components. Prefer Basic/Preferred parts only when the user asks for
that cost tradeoff; package, manufacturer, stock threshold, and the final part
remain caller decisions. Catalogue stock and price are snapshots, not promises.

Search KiCad's installed symbol and footprint libraries before obtaining an
external CAD model. If the required symbol or footprint is absent, use an
interactive browser to download a KiCad bundle from the source the user chooses
(for example SnapEDA/SnapMagic or Component Search Engine). If authentication
is required, ask the user to sign in in that browser; never request a password
in chat, pass credentials to an MCP tool, or store them in project metadata.
Then call `import_project_library`. Assets belong inside the individual project;
never add them to KiCad's global symbol, footprint, or 3D-model directories.
Treat third-party CAD as unverified: compare pins, pads, courtyard, orientation,
and model alignment with the manufacturer datasheet before fabrication.
"""

__all__ = ["PARTS"]
