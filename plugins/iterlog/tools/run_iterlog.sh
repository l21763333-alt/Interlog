#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
RUNTIME="$SCRIPT_DIR/iterlog.py"
export PYTHONUTF8=1
export ITERLOG_HOST=codex

run_if_supported() {
    candidate=$1
    shift
    if command -v "$candidate" >/dev/null 2>&1 \
        && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        exec "$candidate" "$RUNTIME" "$@"
    fi
}

if [ -n "${ITERLOG_PYTHON:-}" ]; then
    run_if_supported "$ITERLOG_PYTHON" "$@"
fi
run_if_supported python3 "$@"
run_if_supported python "$@"

printf '%s\n' 'Iterlog requires Python 3.10+. Set ITERLOG_PYTHON to an absolute interpreter path.' >&2
exit 1
