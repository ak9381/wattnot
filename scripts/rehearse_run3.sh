#!/usr/bin/env bash
# Full RUN3 rehearsal on CPU with mock NVML / GPU / hosted backends. One command runs every block
# of the real RUN3 chain in order, exits 0 when they all ran, and writes a summary JSON. It never
# touches a real GPU or cloud: no NVML is opened, no network call is made, no secret is read.
#
#   bash scripts/rehearse_run3.sh [OUT_DIR]
#
# Safety: this is the dry run, not the launch. It refuses if RUN3_GO is set (that flag is only for
# the real paid run) and it unsets every hosted key so nothing can reach out.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -n "${RUN3_GO:-}" ]; then
  echo "refusing: RUN3_GO is set. This is the mock rehearsal, not the real GPU run." >&2
  exit 2
fi

OUT="${1:-results/rehearse-run3}"
export PYTHONPATH=src
# Belt and braces: strip any hosted credentials from this process so a block cannot call out.
unset HF_TOKEN BASETEN_API_KEY META_API_KEY MODAL_TOKEN_ID MODAL_TOKEN_SECRET E2E_API_KEY 2>/dev/null || true

PY="${PYTHON:-}"
if [ -z "$PY" ]; then for c in python python3; do command -v "$c" >/dev/null 2>&1 && PY="$c" && break; done; fi
[ -n "$PY" ] || { echo "no python interpreter found (set PYTHON=/path/to/python)" >&2; exit 1; }

echo "WattNot RUN3 rehearsal (CPU, mock NVML/GPU/hosted; no network, no GPU, no secrets)"
"$PY" -m wattnot.rehearse --out "$OUT"
code=$?
echo "summary: $OUT/rehearse-summary.json"
exit "$code"
