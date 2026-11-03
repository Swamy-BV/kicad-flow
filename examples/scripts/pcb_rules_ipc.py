"""Check native custom-rule scopes, preservation, refusal and DRC behavior."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp


async def main() -> None:
    """Observe rules on both faces and inner layers through unchanged MCP tools."""
    parent = Path("out/pcb-rules-ipc")
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(mkdtemp(prefix="run-", dir=parent)).resolve()
    path = str(root / "rules.kicad_pcb")
    assets = Path(__file__).resolve().parents[1] / "assets"
    protected = str(root / "protected.kicad_pcb")
    protected_rules = Path(protected).with_suffix(".kicad_dru")
    shutil.copyfile(assets / "native-rules.kicad_dru", protected_rules)
    shutil.copyfile(assets / "native-rule-comments.kicad_dru",
                    Path(path).with_suffix(".kicad_dru"))
    async with Client(mcp) as client:

        async def call(tool: str, **args: Any) -> dict[str, Any]:
            data: dict[str, Any] = (await client.call_tool(tool, args)).data
            assert data.get("ok"), (tool, data)
            return data

        def rule(name: str, layer: str, minimum: float) -> dict[str, Any]:
            return {"name": name, "layer": layer, "condition": "A.Type == 'Track'",
                    "constraints": [{"kind": "track_width", "min": minimum}]}

        async def widths() -> set[float]:
            found = (await call("check_board", path=path))["findings"]
            return {f["y"] for f in found if f["kind"] == "track_width"}

        await call("new_board", path=protected)
        protected_bytes = protected_rules.read_bytes()
        empty = await call("set_board_constraints", path=protected, rules=[])
        assert empty["count"] == 0 and protected_rules.read_bytes() == protected_bytes
        refused = (await client.call_tool("set_board_constraints", {
            "path": protected, "rules": [rule("Added", "F.Cu", 0.3)],
        })).data
        assert not refused["ok"] and "outside rule blocks" in refused["error"]
        assert protected_rules.read_bytes() == protected_bytes
        await call("new_board", path=path, layers=4)
        await call("add_graphics", path=path, graphics=[{
            "kind": "rectangle", "layer": "Edge.Cuts",
            "x1": 10, "y1": 10, "x2": 50, "y2": 45,
        }])
        await call("add_tracks", path=path, tracks=[
            {"x1": 20, "y1": y, "x2": 40, "y2": y,
             "layer": layer, "width": 0.2}
            for layer, y in zip(("F.Cu", "B.Cu", "In1.Cu", "In2.Cu"),
                                (20, 25, 30, 35), strict=True)
        ])
        await call("save_board", path=path)
        expected = {"Preserved note": "outer", "Front": "F.Cu",
                    "Back": "B.Cu", "Inner one": "In1.Cu"}
        await call("set_board_constraints", path=path, rules=[
            rule("Front", "F.Cu", 0.3), rule("Back", "B.Cu", 0.25),
            rule("Inner one", "In1.Cu", 0.35),
        ])
        assert await widths() == {20, 25, 30}
        updated = await call("set_board_constraints", path=path,
                             rules=[rule("Front", "F.Cu", 0.1)])
        assert updated["count"] == 1, updated
        assert await widths() == {25, 30}
        await call("set_board_constraints", path=path, rules=[
            rule("All outer", "outer", 0.4), rule("All inner", "inner", 0.5),
        ])
        expected.update({"All outer": "outer", "All inner": "inner"})
        assert await widths() == {20, 25, 30, 35}
        found = (await call("list_board_constraints", path=path))["rules"]
        assert {r["name"]: r["layer"] for r in found} == expected, found

        rules_file = Path(path).with_suffix(".kicad_dru")
        before = rules_file.read_bytes()
        refused = (await client.call_tool("set_board_constraints", {
            "path": path, "rules": [rule("Front", "F.Cu", 9.9),
                                      rule("Invalid", "MISSING.Layer", 0.4)],
        })).data
        assert not refused["ok"], refused
        assert rules_file.read_bytes() == before

        invalid = {**rule("Front", "F.Cu", 9.9), "condition": "A.Type =="}
        refused = (await client.call_tool("set_board_constraints", {
            "path": path, "rules": [invalid],
        })).data
        assert not refused["ok"], refused
        assert rules_file.read_bytes() == before
        after = (await call("list_board_constraints", path=path))["rules"]
        assert after == found, after
        text = rules_file.read_text(encoding="utf-8")
        assert "Preserve this rule and its comment" in text
        assert "(severity warning)" in text
        await call("reload_board", path=path)
        after = (await call("list_board_constraints", path=path))["rules"]
        assert after == found, after
        assert await widths() == {20, 25, 30, 35}
        await call("render_board_layout", path=path,
                   output_file=str(root / "top.png"))
        print(json.dumps({"ok": True, "directory": str(root),
                          "layer_scopes": expected, "refused_writes": 3}))


if __name__ == "__main__":
    asyncio.run(main())
