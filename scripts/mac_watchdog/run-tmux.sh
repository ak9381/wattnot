#!/usr/bin/env bash
# tmux alternative to the launchd agent for the macOS off-box watchdog (layer 5).
#
# Use this if you would rather watch the loop in a terminal than run it as a background agent.
# It wraps the watchdog in `caffeinate -i` (so the Mac does not idle-sleep the loop away) inside
# a detached tmux session you can re-attach to.
#
# SAFE DEFAULT: dry-run. Pass WATCHDOG_LIVE=1 to arm a real delete — and only after you have
# stored the API key in the keychain (see INSTALL.md). Credentials are read from the keychain or
# the environment at runtime; never put a key in this file or in the repo.
#
# Usage:
#   HEARTBEAT_URL="https://gist.../raw/heartbeat.json" \
#   BUDGET_CAP_INR=8122 MAX_HOURS=18 \
#   ./run-tmux.sh                 # dry-run
#
#   HEARTBEAT_URL=... WATCHDOG_LIVE=1 E2E_VM_ID=... ./run-tmux.sh   # live (needs keychain key)
#
#   tmux attach -t wattnot-watchdog     # watch it
#   tmux kill-session -t wattnot-watchdog   # stop it
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION="wattnot-watchdog"
: "${HEARTBEAT_URL:?set HEARTBEAT_URL to the published heartbeat URL or path}"
: "${POLL_EVERY_S:=120}"

mode="--dry-run"
if [ "${WATCHDOG_LIVE:-0}" = "1" ]; then
  mode="--live"
  echo "WARNING: live mode — a tripped cap WILL delete E2E VM ${E2E_VM_ID:-?}"
fi

args=(
  "$DIR/watchdog.py"
  --heartbeat-source "$HEARTBEAT_URL"
  --poll-every-s "$POLL_EVERY_S"
  "$mode"
)
[ -n "${BUDGET_CAP_INR:-}" ] && args+=(--budget-cap-inr "$BUDGET_CAP_INR")
[ -n "${MAX_HOURS:-}" ] && args+=(--max-hours "$MAX_HOURS")

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "session $SESSION already running; attach with: tmux attach -t $SESSION"
  exit 0
fi

# caffeinate -i prevents idle sleep so the poll loop keeps running on a closed-lid/idle Mac.
tmux new-session -d -s "$SESSION" \
  caffeinate -i /usr/bin/python3 "${args[@]}"
echo "started tmux session '$SESSION' ($mode). attach: tmux attach -t $SESSION"
