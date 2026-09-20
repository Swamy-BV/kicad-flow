"""Exercise JLCPCB manufacturing primitives against the LED fixture through MCP."""

from __future__ import annotations

import asyncio
import csv
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp


async def run() -> None:
    """Export, inspect, detect stale files and archive without touching fixtures."""
    parent = Path("out/manufacturing").resolve()
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(dir=parent))
    for source in Path("examples/led_digits").glob("*.kicad_*"):
        shutil.copyfile(source, root / source.name)
    board = str(root / "led_digits.kicad_pcb")
    sheet = str(root / "led_digits.kicad_sch")
    async with Client(mcp) as client:
        async def call(name: str, **args: Any) -> dict[str, Any]:
            reply = await client.call_tool(name, args)
            data: dict[str, Any] = reply.data
            assert data.get("ok"), (name, data)
            return data

        refused = await client.call_tool("get_manufacturing_requirements", {
            "path": board, "service": "pcb",
        })
        assert not refused.data["ok"]
        await call("set_fabrication_profile", path=board)
        pcb = await call("get_manufacturing_requirements", path=board, service="pcb")
        assert pcb["requirements"]["required_outputs"] == ["gerbers", "drills"]
        refused = await client.call_tool("get_manufacturing_requirements", {
            "path": board, "service": "pcba",
        })
        assert not refused.data["ok"]
        sides = ["front", "back"]
        args = {"path": board, "service": "pcba", "assembly_sides": sides}
        requirements = await call("get_manufacturing_requirements", **args)
        assert "F.Paste" in requirements["requirements"]["gerber"]["layers"]
        gerbers = await call("export_gerbers", **args, output_dir=str(root / "gerbers"))
        drills = await call("export_drills", path=board,
                            output_dir=str(root / "drills"))
        bom_file = str(root / "bom.csv")
        cpl_file = str(root / "cpl.csv")
        await call("export_bom", path=board, schematic_path=sheet,
                   output_file=bom_file, assembly_sides=sides)
        await call("export_placements", path=board, output_file=cpl_file,
                   assembly_sides=sides)
        source_bytes = Path(board).read_bytes()
        files = [f["path"] for f in gerbers["files"] + drills["files"]]
        check_args = {**args, "files": [*files, bom_file, cpl_file],
                      "schematic_path": sheet, "bom_file": bom_file,
                      "placements_file": cpl_file}
        check = await call("check_manufacturing_package", **check_args)
        unexpected = [issue for issue in check["issues"]
                      if issue["code"] != "missing_part_number"]
        assert not unexpected, unexpected
        assert check["issues"], "fixture intentionally has no assembly part numbers"
        Path(root / "check.json").write_text(json.dumps(check, indent=2))
        with Path(cpl_file).open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert rows
        for side, label in (("front", "top"), ("back", "bottom")):
            destination = str(root / f"{side}.csv")
            await call("export_placements", path=board, output_file=destination,
                       assembly_sides=[side])
            with Path(destination).open(newline="") as stream:
                selected = list(csv.DictReader(stream))
            assert selected and all(r["Layer"] == label for r in selected)
            assert selected == [r for r in rows if r["Layer"] == label]
        altered = Path(next(f for f in files if f.endswith(".gtl")))
        saved = altered.read_bytes()
        altered.write_bytes(saved + b"G04 altered copper output*\n")
        stale = await call("check_manufacturing_package", **check_args)
        assert any(i["code"] == "stale_or_different_file"
                   for i in stale["issues"])
        altered.write_bytes(saved)
        original = Path(cpl_file).read_bytes()
        Path(cpl_file).write_bytes(original.replace(b"Designator", b"BadHeader", 1))
        refused = await client.call_tool("check_manufacturing_package", check_args)
        assert not refused.data["ok"]
        Path(cpl_file).write_bytes(original)
        with Path(cpl_file).open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            altered_rows = [dict(r) for r in rows]
            altered_rows[0]["Mid X"] = "999.0"
            writer.writerows(altered_rows)
        changed = await call("check_manufacturing_package", **check_args)
        assert any(i["code"] == "stale_placements" for i in changed["issues"])
        Path(cpl_file).write_bytes(original)
        missing = await call("check_manufacturing_package", **{
            **check_args, "files": [bom_file, cpl_file],
        })
        assert any(i["code"] == "missing_file" for i in missing["issues"])
        four = str(root / "four.kicad_pcb")
        await call("new_board", path=four, layers=4)
        await call("set_fabrication_profile", path=four, inner_copper_oz=0.5)
        inner = await call("get_manufacturing_requirements", path=four, service="pcb")
        assert "In1.Cu" in inner["requirements"]["gerber"]["layers"]
        assert "In2.Cu" in inner["requirements"]["gerber"]["layers"]
        archive = await call("archive_manufacturing_files", files=files,
                             output_zip=str(root / "fabrication.zip"))
        assert archive["count"] == len(files)
        duplicate = await client.call_tool("export_gerbers", {
            **args, "output_dir": str(root / "gerbers"),
        })
        assert not duplicate.data["ok"]
        assert Path(board).read_bytes() == source_bytes
        print(f"Manufacturing example passed: {root}")
        print(f"CAM files: {len(files)}; BOM rows: {len(rows)}; "
              f"reported DRC findings: {len(check['drc'])}")


if __name__ == "__main__":
    asyncio.run(run())
