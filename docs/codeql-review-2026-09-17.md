# Local CodeQL review — 2026-09-17

CodeQL CLI 2.27.0 scanned a clean snapshot of `chore/openssf-baseline` (128
Python files and 3 GitHub Actions workflows). The default code-scanning and
security-extended Python suites each returned **0 alerts** after the monitor's
3D render filename stopped incorporating request data. The broader
`python-security-and-quality.qls` suite returned **125 findings** after review.

| Rule | Count | Disposition |
| --- | ---: | --- |
| `py/unused-import` | 70 | `server/tools_board.py` and `server/tools_schematic.py` deliberately re-export names for existing callers. Their module docstrings state this compatibility role. Removing imports would change that interface. |
| `py/cyclic-import` and `py/unsafe-cyclic-import` | 37 | The reported back-edges to `KiCadBoard` and `KiCadSheet` are inside `if TYPE_CHECKING:` blocks; they do not execute at runtime. The facade imports the implementation modules, while those modules need the facade class only for annotations. |
| `py/unused-global-variable` | 14 | Eleven constants are used by other backend modules; the three `_ensured_url` locations are assignments and reads within `ensure_running`. One actually unused parser constant, `_WHITESPACE`, was removed. |
| `py/ineffectual-statement` | 3 | These `await` expressions join cancelled tasks so cancellation completes before cleanup or assertions. Their return values are intentionally unused. |
| `py/empty-except` | 1 | The activity logger intentionally suppresses `CancelledError` when its slow-call timer is cancelled after a tool finishes. The monitor's image fallback has an explanatory comment and no longer triggers this query. |

These are dispositions of the local findings, not a claim that static analysis
proves the absence of vulnerabilities. The GitHub CodeQL workflow uses the
default security suite; its hosted result still needs to run after the workflow
reaches GitHub.
