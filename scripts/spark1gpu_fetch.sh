#!/usr/bin/env bash
# Download the Spark Open GGUF (UD-IQ2_S, 276 GB; UD-IQ1_S, 176 GB, if IQ2_S will not fit).
#   bash scripts/spark1gpu_fetch.sh --dry-run               # show the plan, download nothing
#   HF_TOKEN=... bash scripts/spark1gpu_fetch.sh            # resumes if interrupted
# Env: MODELS_DIR (default /data/spark-open). HF_TOKEN is read by `hf` from the environment.
# Needs `pip install -U huggingface_hub` for the `hf` command.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-src}"
exec python3 -m wattnot.spark_open.fetch --models-dir "${MODELS_DIR:-/data/spark-open}" "$@"
