# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata


ROOT = Path(SPECPATH).resolve().parents[1]
SRC = ROOT / "src"

datas = [
    (str(SRC / "kicad_flow" / "monitor" / "static"), "kicad_flow/monitor/static"),
    (
        str(SRC / "kicad_flow" / "providers" / "jlcpcb" / "capabilities.json"),
        "kicad_flow/providers/jlcpcb",
    ),
]
for distribution in ("fastmcp", "fastmcp-slim", "mcp", "PyMuPDF"):
    datas += copy_metadata(distribution)
binaries = []
hiddenimports = []

# FastMCP registers transports and serializers dynamically. Collecting its own
# package is less brittle than maintaining a private list of those imports.
for package in ("fastmcp", "mcp"):
    package_datas, package_binaries, package_imports = collect_all(
        package,
        filter_submodules=lambda name: not name.startswith("mcp.cli"),
    )
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports

a = Analysis(
    [str(ROOT / "packaging" / "windows" / "entrypoint.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["mypy", "pytest", "ruff", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="kicad-flow",
    console=True,
    upx=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="kicad-flow",
)
