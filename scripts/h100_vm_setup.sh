#!/usr/bin/env bash
# GPU host setup for the orchestrated proof run (scripts run from a control machine over SSH).
# Installs the kit and vLLM into /root/venv, starts the model server in the background and
# waits until it answers. Prints READY when the card and the server are up.
#   HF_TOKEN=... bash scripts/h100_vm_setup.sh [MODEL] [MOE_MODEL]
# The token is only read from the environment and never written to a log.
set -euo pipefail
MODEL="${1:-meta-llama/Llama-3.1-8B-Instruct}"
MOE="${2:-ibm-granite/granite-3.1-3b-a800m-instruct}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG=/root/run2
mkdir -p "$LOG"
echo "[setup] $(date +%T) start"
command -v nvidia-smi >/dev/null || { echo "no nvidia-smi: not a GPU host"; exit 10; }
nvidia-smi --query-gpu=name,driver_version,power.limit,memory.total --format=csv,noheader
df -h / /root | tail -n 2
command -v python3 >/dev/null && python3 -c "import venv" 2>/dev/null || {
  DEBIAN_FRONTEND=noninteractive apt-get update -q && \
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3-venv python3-pip git curl; }
nvidia-smi -pm 1 >/dev/null 2>&1 || true
python3 -m venv /root/venv
/root/venv/bin/pip install -q uv
export UV_LINK_MODE=copy
/root/venv/bin/uv pip install -q --python /root/venv/bin/python -e "$REPO" \
  "huggingface_hub>=0.30" vllm
echo "[setup] $(date +%T) packages installed"
df -h / | tail -n 1
if [ -n "${HF_TOKEN:-}" ]; then export HUGGING_FACE_HUB_TOKEN="$HF_TOKEN"; fi
/root/venv/bin/python - "$MODEL" <<'PY'
import sys
from huggingface_hub import snapshot_download
snapshot_download(sys.argv[1], allow_patterns=["*.json", "*.safetensors", "*.txt", "tokenizer*"])
print("[setup] weights ready:", sys.argv[1])
PY
nohup /root/venv/bin/vllm serve "$MODEL" --port 8000 --max-model-len 4096 \
  --gpu-memory-utilization 0.90 --max-num-seqs 64 --served-model-name "$MODEL" \
  > "$LOG/vllm.log" 2>&1 &
for i in $(seq 1 180); do
  if curl -fs http://127.0.0.1:8000/v1/models >/dev/null 2>&1; then break; fi
  sleep 5
done
curl -fs http://127.0.0.1:8000/v1/models >/dev/null || { echo "[setup] server did not come up"; tail -n 30 "$LOG/vllm.log"; exit 11; }
# the MoE model for the adaptive step downloads in the background while the sweep runs
nohup /root/venv/bin/python - "$MOE" > "$LOG/moe_download.log" 2>&1 <<'PY' &
import sys
from huggingface_hub import snapshot_download
snapshot_download(sys.argv[1], allow_patterns=["*.json", "*.safetensors", "*.txt", "tokenizer*"])
print("moe weights ready:", sys.argv[1])
PY
df -h / | tail -n 1
echo "[setup] $(date +%T) READY"
