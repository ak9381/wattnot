#!/usr/bin/env bash
# The real side-demo run. Run it ON a GPU box you already rented and will stop yourself.
# This script never rents or stops a GPU.
#   export HF_TOKEN=...             # for the download
#   export BASETEN_API_KEY=...      # only if BASETEN=live
#   bash scripts/spark1gpu_run.sh [--baseten live|replay|off] [--planner fallback|muse]
# Checks first: a GPU is visible, 250 GB RAM free-ish, and enough disk for the quant.
# Results: results/spark1gpu-<time>/{tune.json,h2h.json} (copy them back before you stop the box).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-src}"
MODELS_DIR="${MODELS_DIR:-/data/spark-open}"
command -v nvidia-smi >/dev/null || { echo "no nvidia-smi: this is the GPU-box script"; exit 10; }
command -v llama-server >/dev/null || { echo "llama-server missing: bash scripts/spark1gpu_build_llama.sh"; exit 10; }
RAM_GB=$(awk '/MemTotal/ {printf "%d", $2/1048576}' /proc/meminfo)
[ "$RAM_GB" -ge 240 ] || { echo "only ${RAM_GB} GB RAM; IQ2_S needs about 250 (use UD-IQ1_S via --quant)"; }
OUT="results/spark1gpu-$(date -u +%Y%m%d-%H%M%S)"
python3 -m wattnot.spark_open.side --mode real --yes-gpu-is-running \
  --models-dir "$MODELS_DIR" --out "$OUT" --baseten replay --planner fallback "$@"
echo "results in $OUT"
