#!/usr/bin/env bash
# GPU-host probe / go-no-go, run once before any measured block in the 2 h Glimmer test.
#   bash scripts/glimmer_probe.sh [BASE_URL]
# Two real checks on the live card and the live server:
#   1. power-cap write: set a safe cap, read it back, restore the default. If the cap cannot be
#      set, the whole power-sweep proof is impossible, so stop now (exit 12).
#   2. image acceptance: send ONE small real image to the served model over the /chat/completions
#      route and fail (exit 2) if rejected, so the eye test never runs blind on a text-only server.
# No model weights or paid calls here; it only pokes the card and the already-running server.
set -euo pipefail
BASE_URL="${1:-http://127.0.0.1:8000/v1}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-${VIRTUAL_ENV:-/root/venv}/bin/python}"
command -v "$PY" >/dev/null 2>&1 || PY=python3

command -v nvidia-smi >/dev/null || { echo "[probe] no nvidia-smi: not a GPU host"; exit 10; }
DEF_W="$(nvidia-smi --query-gpu=power.default_limit --format=csv,noheader,nounits | head -n1 | cut -d. -f1 | tr -d ' ')"
TEST_W=$(( DEF_W > 600 ? 600 : DEF_W - 50 ))
if ! nvidia-smi -pl "$TEST_W" >/dev/null 2>&1; then
  echo "[probe] cannot set a power cap ($TEST_W W): the power-sweep proof cannot run"; exit 12
fi
GOT="$(nvidia-smi --query-gpu=power.limit --format=csv,noheader,nounits | head -n1 | cut -d. -f1 | tr -d ' ')"
nvidia-smi -pl "$DEF_W" >/dev/null 2>&1 || { echo "[probe] could not restore the cap to $DEF_W W"; exit 12; }
[ "$GOT" = "$TEST_W" ] || { echo "[probe] cap read back $GOT W, asked $TEST_W W"; exit 12; }
echo "[probe] power cap ok: set $TEST_W W, read back $GOT W, restored $DEF_W W"

# image acceptance against the live server (real call; fails clearly if the image is rejected)
"$PY" -m wattnot.vision.imageprobe --base-url "$BASE_URL"
echo "[probe] go: cap write + image acceptance both passed"
