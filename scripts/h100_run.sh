#!/usr/bin/env bash
# One command for the rented H100 (T25). Dry run first, anywhere:
#   bash scripts/h100_run.sh --dry-run
# On the GPU box (root):
#   export HF_TOKEN=...            # read access; Llama 3.1 licence accepted on HF
#   export META_API_KEY=...        # optional: one Muse Spark call seeds Optuna (--spark)
#   bash scripts/h100_run.sh [--gpus 2] [--spark]
# Exit codes: 0 ok, 10 setup failed, 20 probe failed -> release the machine, 30 stage failed.
# Results: results/h100-<time>/ plus results/h100-<time>.tar.gz to copy back.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="results/h100-$(date -u +%Y%m%d-%H%M%S)"
RIG=real; EXTRA=()
for a in "$@"; do
  case "$a" in
    --dry-run) RIG=fake; EXTRA+=(--quick) ;;
    *) EXTRA+=("$a") ;;
  esac
done
if [ ! -d .venv ]; then python3 -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate
PIP="python -m pip"; command -v uv >/dev/null && PIP="uv pip"
$PIP install -q -e . "huggingface_hub[cli]>=0.30"
if [ "$RIG" = real ]; then
  : "${HF_TOKEN:?set HF_TOKEN (Hugging Face read token) first}"
  command -v nvidia-smi >/dev/null || { echo "no nvidia-smi: not a GPU host"; exit 10; }
  command -v docker >/dev/null || echo "warning: docker missing; Muse Glimmer needs vllm/vllm-openai:muse-glimmer"
  python -c "import vllm" 2>/dev/null || $PIP install -q vllm  # for Llama 3.1 8B
fi
set +e
python -m wattnot.runbook --rig "$RIG" --out "$OUT" "${EXTRA[@]}"
code=$?
set -e
echo "results: $OUT  archive: $OUT.tar.gz  exit: $code"
[ "$code" = 20 ] && echo ">>> PROBE FAILED: release the machine now."
exit "$code"
