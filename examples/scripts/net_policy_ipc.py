"""Check native class ownership and independently inherited via dimensions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from _board_fixture import export_footprints, schematic_nets
from fastmcp import Client

from kicad_flow.server import mcp


async def check(root: Path, *, reopen: bool) -> None:
    """Observe explicit, composite, patterned and default netclasses through MCP."""
    path = str(root / "policy.kicad_pcb")
    async with Client(mcp) as client:

        async def call(tool: str, **args: Any) -> dict[str, Any]:
            data: dict[str, Any] = (await client.call_tool(tool, args)).data
            assert data.get("ok"), (tool, data)
            return data

        async def policies() -> dict[str, Any]:
            data = await call("list_board_nets", path=path, include_rules=True)
            result = data["routing_policy"]
            assert not result["readback_warnings"], result
            return {r["net"]: r for r in result["nets"]}

        expected_file = root / "expected.json"
        if reopen:
            expected = json.loads(expected_file.read_text(encoding="utf-8"))
            assert await policies() == expected
            print("PASS native netclass policy in a fresh process", flush=True)
            return
        await call("new_board", path=path)
        await export_footprints(call, path, [{
            "ref": "J1", "lib_id": "Connector_Generic:Conn_01x04",
            "fp_id": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
            "x": 20, "y": 20,
        }])
        await schematic_nets(call, path, [
            {"ref": "J1", "pad": str(i), "net": name}
            for i, name in enumerate(("ONE", "PAIR", "PATTERN", "DEFAULT"), 1)
        ])
        await call("set_net_classes", path=path, classes=[
            {"name": "Alpha", "track_width": 0.3, "via_diameter": 0.8,
             "pcb_color": "#22446680"},
            {"name": "Beta", "track_width": 0.45, "clearance": 0.35,
             "via_drill": 0.4},
        ])
        await call("assign_net_classes", path=path, assignments=[
            {"net": "ONE", "net_class": "Alpha"},
            {"net": "PAIR", "net_class": "Beta"},
            {"net": "PAIR", "net_class": "Alpha"},
        ])
        await call("set_net_class_patterns", path=path,
                   patterns=[{"pattern": "PAT*", "net_class": "Beta"}])
        result = await policies()
        assert result["ONE"]["sources"]["track_width"] == "Alpha", result
        assert result["ONE"]["sources"]["clearance"] == "Default", result
        assert result["ONE"]["sources"]["via_drill"] == "Default", result
        assert result["PAIR"]["sources"]["track_width"] == "Alpha", result
        assert result["PAIR"]["sources"]["via_diameter"] == "Alpha", result
        assert result["PAIR"]["sources"]["via_drill"] == "Beta", result
        assert result["PAIR"]["sources"]["clearance"] == "Beta", result
        assert result["PATTERN"]["sources"]["track_width"] == "Beta", result
        assert result["DEFAULT"]["sources"]["track_width"] == "Default", result
        assert result["ONE"]["class_colors"]["pcb_color"] == "#22446680", result

        classes = (await call("list_net_classes", path=path))["classes"]
        by_name = {c["name"]: c for c in classes}
        assert "via_drill" not in by_name["Alpha"], by_name
        assert "via_diameter" not in by_name["Beta"], by_name
        await call("set_net_classes", path=path, classes=[
            {"name": "Default", "via_drill": 0.33},
            {"name": "Alpha", "via_diameter": 0.9},
        ])
        changed = await policies()
        assert changed["ONE"]["dimensions"]["via_drill"] == 0.33, changed
        assert changed["ONE"]["dimensions"]["via_diameter"] == 0.9, changed
        assert changed["PAIR"]["dimensions"]["via_drill"] == 0.4, changed
        refused = (await client.call_tool("set_net_classes", {
            "path": path, "classes": [
                {"name": "Alpha", "track_width": 9.9},
                {"name": "Unverifiable", "microvia_diameter": 0.55},
            ],
        })).data
        assert not refused["ok"] and "microvia" in refused["error"], refused
        assert await policies() == changed
        refused = (await client.call_tool("set_net_classes", {
            "path": path, "classes": [
                {"name": "Alpha", "track_width": 9.9},
                {"name": "Invalid", "via_diameter": 0.3, "via_drill": 0.4},
            ],
        })).data
        assert not refused["ok"] and "drill exceeds diameter" in refused["error"]
        assert await policies() == changed
        files = [Path(path), Path(path).with_suffix(".kicad_pro")]
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
        assert await policies() == changed
        assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in files] == before
        await call("save_board", path=path)
        await call("reload_board", path=path)
        assert await policies() == changed
        expected_file.write_text(json.dumps(changed), encoding="utf-8")
        print(json.dumps({"ok": True, "directory": str(root),
                          "nets": len(result), "verified_sources": True}))


def main() -> None:
    """Verify persisted policy after the original native host exits."""
    if len(sys.argv) == 3:
        asyncio.run(check(Path(sys.argv[2]), reopen=sys.argv[1] == "reopen"))
        return
    parent = Path("out/net-policy-ipc")
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(mkdtemp(prefix="run-", dir=parent)).resolve()
    for phase in ("build", "reopen"):
        subprocess.run([sys.executable, __file__, phase, str(root)], check=True)


if __name__ == "__main__":
    main()
