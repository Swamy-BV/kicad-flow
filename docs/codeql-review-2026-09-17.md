# Local CodeQL review — 2026-09-17

CodeQL CLI 2.27.0 scanned a clean snapshot matching the branch's Python source
(130 Python files and 3 GitHub Actions workflows). The full
`python-security-and-quality.qls` suite returned **0 alerts**. The GitHub
workflow now runs the matching `security-and-quality` query suite so future
pull requests exercise the same breadth. No queries were excluded and no
findings were suppressed.

The original broad scan reported 125 findings. The changes make existing
compatibility exports and shared constants explicit with `__all__`, replace
helper-to-facade type imports with typed internal state protocols, and let the
facades create their own isolated copies and child sheets. Monitor startup
state is guarded against concurrent calls. Cancelled-task joins now check for
unexpected errors, and the best-effort replay logger explains its exception
handler. These changes preserve the primitive MCP tool catalog; the
`led_digits.py` example exercised 102/102 tools with 0 failed calls.

Ruff and strict mypy passed, as did `fc.py`, `led_digits.py`,
`board_net_sync.py`, `routing_preview.py`, `layout_feedback.py`, and
`mcp_execution.py` through MCP. A zero static-analysis result does not prove
the absence of vulnerabilities; the hosted GitHub CodeQL run remains to be
observed when this branch is published.
