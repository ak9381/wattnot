#!/usr/bin/env bash
# Start a paid Modal runner fully detached from the calling shell (its own session, no hangup):
#   WATTNOT_MODAL_GO=1 scripts/e2e/launch_detached.sh LOG -- python scripts/e2e/modal_block_run.py --attach r04e --only langchain
# Prints the PID. Pair with `modal run --detach` inside the runner so neither a shell exit nor a
# dropped client stops the app.
set -euo pipefail
log="$1"; shift
[ "${1:-}" = "--" ] && shift
setsid nohup "$@" > "$log" 2>&1 < /dev/null &
echo "$!"
