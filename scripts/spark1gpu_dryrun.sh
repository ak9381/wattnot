#!/usr/bin/env bash
# CPU dry run of the whole side demo with a tiny GGUF. Costs nothing, rents nothing.
# The local numbers prove the plumbing works; they say nothing about Spark Open on an H100.
#   TINY_GGUF=/path/small.gguf bash scripts/spark1gpu_dryrun.sh
# Baseten is REPLAYED from data/cloud/recorded-smoke.json (no network, no key).
# Needs llama-server on PATH (bash scripts/spark1gpu_build_llama.sh --cpu).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-src}"
: "${TINY_GGUF:?set TINY_GGUF to a small .gguf file}"
command -v llama-server >/dev/null || { echo "llama-server not on PATH"; exit 10; }
WORK="${WORK:-$(mktemp -d)}"
mkdir -p "$WORK/models/UD-IQ2_S"
ln -sf "$(readlink -f "$TINY_GGUF")" "$WORK/models/UD-IQ2_S/tiny.gguf"
python3 -m wattnot.spark_open.fetch --models-dir "$WORK/fetch" --dry-run --free-gb 600 || true
python3 -m wattnot.spark_open.side --mode dry --models-dir "$WORK/models" --out "${OUT:-$WORK/out}" \
  --planner "${PLANNER:-mock}" --threads "${THREADS:-6}" --baseten replay
