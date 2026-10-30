#!/usr/bin/env bash
# Optional batch-size / KV-cache sweep for Muse Glimmer on one H100.
#   SEQS="32 64 128" KV="auto fp8" bash scripts/batch_kv_sweep.sh [bf16|fp8] [out_dir]
# For each (max-num-seqs, kv-cache-dtype) combo it restarts the server with those knobs and runs a
# short REAL harness measurement, so throughput / p99 / KV headroom can be compared. Real serving
# and real metering, never --fake. This is OFF in the 2 h plan; the driver runs it only as an
# opt-in block, and only when time allows (it costs one server restart per combo).
set -euo pipefail
PREC="${1:-bf16}"; OUT="${2:-/root/run2/results/batch-kv-sweep}"
SEQS="${SEQS:-32 64 128}"
KV="${KV:-auto}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-${VIRTUAL_ENV:-/root/venv}/bin/python}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
CFG="muse-glimmer-30b"; [ "$PREC" = fp8 ] && CFG="muse-glimmer-30b-fp8"
mkdir -p "$OUT"
echo "[sweep] prec=$PREC seqs=[$SEQS] kv=[$KV] -> $OUT"
for s in $SEQS; do
  for k in $KV; do
    tag="seqs${s}-kv${k}"
    kvarg="$k"; [ "$k" = auto ] && kvarg=""
    echo "[sweep] $tag: restarting Glimmer (max-num-seqs=$s, kv-cache-dtype=${k})"
    MAX_NUM_SEQS="$s" KV_CACHE_DTYPE="$kvarg" bash "$REPO/scripts/glimmer_serve.sh" "$PREC" 8000
    "$PY" -m wattnot.longrun.harness --cap 350 --model-config "$CFG" --per-tier 2 \
      --url http://127.0.0.1:8000/v1/chat/completions --out "$OUT/$tag"
  done
done
echo "[sweep] done -> $OUT"
