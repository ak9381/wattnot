#!/usr/bin/env bash
# Power-cap proof run on one GPU host: paired, interleaved stock/capped blocks, graded by the
# answer-checked gate (workloads/proof-v1.jsonl), energy from the GPU's own counter.
#   bash scripts/h100_proof.sh [--model REPO] [--out DIR] [--planner muse|bisect] [--reps 3]
# Needs: an NVIDIA GPU with a writable power limit (root), vLLM, and HF access for gated models.
# Muse Spark picks caps when a Meta key is on this host (META_API_KEY or ~/.config/muse/auth.json);
# otherwise a bisect rule does. Spend is capped by wattnot.meta.ledger. The limit is put back at exit.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -d .venv ]; then python3 -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate
PIP="python -m pip"; command -v uv >/dev/null && PIP="uv pip"
$PIP install -q -e . "huggingface_hub[cli]>=0.30"
python -c "import vllm" 2>/dev/null || $PIP install -q vllm
command -v nvidia-smi >/dev/null || { echo "no nvidia-smi: not a GPU host"; exit 10; }
exec python -m wattnot.proof.run full --serve "$@"
