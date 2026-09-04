#!/bin/sh
# Shared helper for the repo's git hooks: locate the interpreter and run a
# named gate step, printing a readable pass/fail line.
#
# The venv is preferred because that is where `pip install -e ".[dev]"` puts
# ruff/mypy/pytest; a bare `python` on PATH is the fallback so the hooks still
# work in a container or CI-like shell.

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root" || exit 1

if [ -x ".venv/Scripts/python.exe" ]; then       # Windows venv
    PY=".venv/Scripts/python.exe"
elif [ -x ".venv/bin/python" ]; then             # POSIX venv
    PY=".venv/bin/python"
elif command -v python >/dev/null 2>&1; then
    PY="python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    echo "hook: no Python interpreter found; skipping checks" >&2
    exit 0
fi

# Bail out politely if the dev tooling is not installed, rather than failing a
# commit with an opaque ModuleNotFoundError.
if ! "$PY" -c "import ruff" >/dev/null 2>&1 && ! "$PY" -m ruff --version >/dev/null 2>&1; then
    echo "hook: ruff not installed (pip install -e \".[dev]\"); skipping checks" >&2
    exit 0
fi

# Truthy test for the hook's skip switches.
_truthy() {
    case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
        1|on|true|yes) return 0 ;;
        *) return 1 ;;
    esac
}

# KICAD_FLOW_SKIP_GATE=1 turns every hook into a no-op (lint, types AND tests).
# Prefer the narrower KICAD_FLOW_SKIP_TESTS=1 when you only want to defer the
# slow suite -- lint and mypy still run, so the cheap feedback is not lost.
if _truthy "$KICAD_FLOW_SKIP_GATE"; then
    echo "hook: KICAD_FLOW_SKIP_GATE set; skipping all checks" >&2
    exit 0
fi

fail=0

run_step() {
    label=$1
    shift
    if output=$("$PY" -m "$@" 2>&1); then
        printf '  %-22s ok\n' "$label"
    else
        printf '  %-22s FAILED\n' "$label"
        printf '%s\n' "$output" | sed 's/^/    /'
        fail=1
    fi
}
