"""Exercise the dashboard's MCP bridge and HTTP boundary without model access."""

from __future__ import annotations

import http.client
import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from kicad_flow.agent.client import AgentClient
from kicad_flow.agent.tools import invoke
from kicad_flow.monitor import _make_httpd


def main() -> None:
    """Build through MCP and prove Ask, path and browser-origin restrictions."""
    output = Path("out/agent-workspace").resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="check-", dir=output))

    def call(name: str, read_only: bool = False, **arguments: Any) -> dict[str, Any]:
        reply = invoke(
            "kicad_call",
            {"name": name, "arguments": arguments},
            root,
            read_only=read_only,
        )
        assert reply["success"], reply
        data: dict[str, Any] = json.loads(reply["contentItems"][0]["text"])
        return data

    found = invoke("kicad_search", {"query": "new_board"}, root)
    assert json.loads(found["contentItems"][0]["text"])[0]["name"] == "new_board"
    call("new_board", path="chat.kicad_pcb")
    call(
        "add_graphics",
        path="chat.kicad_pcb",
        graphics=[
            {
                "kind": "rectangle",
                "layer": "Edge.Cuts",
                "x1": 0,
                "y1": 0,
                "x2": 30,
                "y2": 20,
            }
        ],
    )
    call("save_board", path="chat.kicad_pcb")
    assert call("list_graphics", True, path="chat.kicad_pcb")["count"] == 1
    for path, read_only in (
        ("denied.kicad_pcb", True),
        ("../escaped.kicad_pcb", False),
    ):
        denied = invoke(
            "kicad_call",
            {
                "name": "new_board",
                "arguments": {
                    "path": path,
                },
            },
            root,
            read_only=read_only,
        )
        assert not denied["success"], denied
        assert not (root / path).exists()
    call("render_board_layout", path="chat.kicad_pcb", output_file="chat.png")
    viewed = invoke("kicad_view_image", {"path": "chat.png"}, root, read_only=True)
    assert viewed["success"] and viewed["contentItems"][0]["type"] == "inputImage"
    assert not invoke("kicad_view_image", {"path": "../other.png"}, root)["success"]
    call("new_sheet", path="chat.kicad_sch")
    denied = invoke(
        "kicad_call",
        {
            "name": "add_sheets",
            "arguments": {
                "path": "chat.kicad_sch",
                "sheets": [
                    {
                        "name": "Escape",
                        "filename": "../escaped.kicad_sch",
                        "x": 50.8,
                        "y": 50.8,
                    }
                ],
            },
        },
        root,
    )
    assert not denied["success"] and "Child sheet" in str(denied)

    server = _make_httpd(0, root / "activity.jsonl")
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(
        method: str, route: str, data: Any = None, headers: dict[str, str] | None = None
    ) -> tuple[int, Any]:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            conn.request(
                method,
                route,
                json.dumps(data) if data is not None else None,
                headers=headers or {},
            )
            response = conn.getresponse()
            body = response.read()
            return response.status, (
                json.loads(body)
                if response.getheader("Content-Type", "").startswith("application/json")
                else body.decode()
            )
        finally:
            conn.close()

    try:
        code, state = request("GET", "/agent/state")
        assert code == 200 and not state["connected"]
        assert state["service"] == "kicad-flow-agent" and state["protocol"] == 1
        for route in ("/", "/workspace", "/workspace.js", "/workspace.css"):
            assert request("GET", route)[0] == 200, route
        headers = {
            "Content-Type": "application/json",
            "X-KiCadFlow-Chat": state["csrf"],
        }
        assert (
            request("GET", "/agent/state", headers={"Origin": "https://example.com"})[0]
            == 403
        )
        assert (
            request("GET", "/agent/state", headers={"Host": "evil.invalid"})[0] == 403
        )
        assert request("POST", "/agent/project", {"path": str(root)})[0] == 403
        assert (
            request(
                "POST",
                "/agent/project",
                {"path": str(root)},
                {**headers, "Origin": "https://example.com"},
            )[0]
            == 403
        )
        assert request("POST", "/agent/project", {"path": str(root)}, headers)[0] == 200
        code, state = request("GET", "/agent/state")
        assert code == 200 and state["project"] == str(root)
        assert not state["connected"]
        code, result = request(
            "POST",
            "/agent/send",
            {
                "text": "Change the board",
                "model": "unconnected",
                "mode": "edit",
            },
            headers,
        )
        assert code == 400 and "Codex account" in result["error"]
        client = AgentClient(server.server_port)
        client.ensure_running()
        assert client.state()["project"] == str(root)
        client.action("new")
        assert request("GET", "/agent/state")[1]["messages"] == []
        command = subprocess.run(
            [
                sys.executable,
                "-m",
                "kicad_flow.agent",
                "--port",
                str(server.server_port),
                "status",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert json.loads(command.stdout)["project"] == str(root)
        assert request("POST", "/agent/disconnect", {}, headers)[0] == 200
        assert not client.state()["connected"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    print("PASS: MCP edits/readback/render; Ask/paths/origins; shared GUI and CLI")


if __name__ == "__main__":
    main()
