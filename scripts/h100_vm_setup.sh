#!/usr/bin/env bash
# GPU host setup for the orchestrated proof run (scripts run from a control machine over SSH).
# Installs the kit and vLLM into /root/venv, starts the model server in the background and
# waits until it answers. Prints READY when the card and the server are up.
#   HF_TOKEN=... bash scripts/h100_vm_setup.sh [MODEL] [MOE_MODEL]
# The token is only read from the environment and never written to a log.
# Fails fast (before the slow installs) when: no GPU, the power cap cannot be set, the disk is
# too small, or the Hugging Face token cannot read the gated model.
#   SETUP_PREFLIGHT_ONLY=1   run only those checks, then stop
set -euo pipefail
MODEL="${1:-meta-llama/Llama-3.1-8B-Instruct}"
MOE="${2:-ibm-granite/granite-3.1-3b-a800m-instruct}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG=/root/run2
MIN_FREE_GB="${MIN_FREE_GB:-60}"
mkdir -p "$LOG"
echo "[setup] $(date +%T) start"
command -v nvidia-smi >/dev/null || { echo "no nvidia-smi: not a GPU host"; exit 10; }
nvidia-smi --query-gpu=name,driver_version,power.limit,memory.total --format=csv,noheader
# whole-session GPU trace, 1 Hz: idle power during the installs, temperatures, throttle reasons
THR=clocks_throttle_reasons.active   # newer drivers call it clocks_event_reasons.active
nvidia-smi --query-gpu=$THR --format=csv,noheader >/dev/null 2>&1 || THR=clocks_event_reasons.active
nohup nvidia-smi -l 1 --format=csv \
  --query-gpu=timestamp,power.draw,power.limit,temperature.gpu,temperature.memory,clocks.sm,clocks.mem,utilization.gpu,memory.used,$THR \
  > "$LOG/gpu_trace.csv" 2>&1 &
nvidia-smi -q > "$LOG/nvsmi_full.txt" 2>&1 || true
nvidia-smi -q -d POWER,CLOCK,TEMPERATURE,PERFORMANCE > "$LOG/nvsmi_power.txt" 2>&1 || true
nvidia-smi -pm 1 >/dev/null 2>&1 || true

# --- fail-fast checks -------------------------------------------------------------------
DEF_W="$(nvidia-smi --query-gpu=power.default_limit --format=csv,noheader,nounits | head -n1 | cut -d. -f1 | tr -d ' ')"
TEST_W=$(( DEF_W > 600 ? 600 : DEF_W - 50 ))
if ! nvidia-smi -pl "$TEST_W" >/dev/null 2>&1; then
  echo "[setup] cannot set a power cap ($TEST_W W) as $(id -un): the run cannot work"; exit 12
fi
GOT="$(nvidia-smi --query-gpu=power.limit --format=csv,noheader,nounits | head -n1 | cut -d. -f1 | tr -d ' ')"
nvidia-smi -pl "$DEF_W" >/dev/null 2>&1 || { echo "[setup] could not restore the cap to $DEF_W W"; exit 12; }
if [ "$GOT" != "$TEST_W" ]; then echo "[setup] cap read back $GOT W, asked $TEST_W W"; exit 12; fi
echo "[setup] power cap check ok: set $TEST_W W, read back $GOT W, restored $DEF_W W"
FREE_GB="$(df -BG --output=avail / | tail -n1 | tr -dc 0-9)"
echo "[setup] free disk: ${FREE_GB} GB (need $MIN_FREE_GB)"
[ "$FREE_GB" -ge "$MIN_FREE_GB" ] || { echo "[setup] disk too small"; exit 14; }
if [ -n "${HF_TOKEN:-}" ] && [ "${MODEL%%/*}" = "meta-llama" ]; then
  code="$(curl -s -o /dev/null -w '%{http_code}' -L -H "Authorization: Bearer $HF_TOKEN" \
    "https://huggingface.co/$MODEL/resolve/main/config.json" || true)"
  echo "[setup] Hugging Face access to $MODEL: HTTP $code"
  [ "$code" = 200 ] || { echo "[setup] token cannot read $MODEL (gated?)"; exit 13; }
fi
[ "${SETUP_PREFLIGHT_ONLY:-0}" = 1 ] && { echo "[setup] preflight ok"; exit 0; }

# --- install ------------------------------------------------------------------------------
df -h / /root | tail -n 2
# a venv from the system python needs ensurepip, which the stock image lacks and apt can stall
# on a fresh VM; fall back to uv (its own python build, with headers) when it fails
if ! python3 -m venv /root/venv >/dev/null 2>&1; then
  echo "[setup] python3 -m venv failed; using uv"
  rm -rf /root/venv
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
  uv venv --seed --python 3.12 /root/venv
fi
/root/venv/bin/pip install -q uv
# RUN2 first-boot problems, each cost minutes: vLLM's JIT needs ninja on PATH, Triton needs
# Python.h (the stock image has no headers), and the Hugging Face xet transfer stalled on large
# shards while plain HTTP ran at ~200 MB/s.
export PATH="/root/venv/bin:$HOME/.local/bin:$PATH"
export HF_HUB_DISABLE_XET=1
if [ ! -f /usr/include/python3.12/Python.h ]; then
  command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
  uv python install 3.12 >/dev/null 2>&1 || true
  H="$(ls -d "$HOME"/.local/share/uv/python/*/include/python3.12 2>/dev/null | head -n1)"
  if [ -n "$H" ]; then mkdir -p /usr/include/python3.12 && cp -r "$H"/. /usr/include/python3.12/; fi
fi
export UV_LINK_MODE=copy UV_NO_CACHE=1   # no second copy of the wheels on a small disk
# accelerate: the adaptive step loads the MoE model with device_map="cuda", which needs it
/root/venv/bin/uv pip install -q --python /root/venv/bin/python -e "$REPO" \
  "huggingface_hub>=0.30" accelerate vllm
echo "[setup] $(date +%T) packages installed"
/root/venv/bin/python - > "$LOG/versions.txt" 2>&1 <<'PY' || true
import importlib.metadata as m
for p in ("vllm", "torch", "transformers", "accelerate", "huggingface_hub", "tokenizers"):
    try:
        print(p, m.version(p))
    except Exception as e:
        print(p, "missing", e)
PY
cat "$LOG/versions.txt"
df -h / | tail -n 1
if [ -n "${HF_TOKEN:-}" ]; then export HUGGING_FACE_HUB_TOKEN="$HF_TOKEN"; fi
export HF_HUB_DOWNLOAD_TIMEOUT=60

cat > "$LOG/dl.py" <<'PY'
import sys, time
from huggingface_hub import snapshot_download
repo = sys.argv[1]
for attempt in range(1, 5):  # a stalled shard resumes on the next try (seen in RUN1)
    try:
        snapshot_download(repo, allow_patterns=["*.json", "*.safetensors", "*.txt", "tokenizer*"])
        print("[setup] weights ready:", repo, flush=True)
        break
    except Exception as e:  # noqa: BLE001
        print(f"[setup] download attempt {attempt} failed: {type(e).__name__}: {str(e)[:160]}",
              flush=True)
        time.sleep(5)
else:
    sys.exit(1)
PY
# the MoE model for the adaptive step downloads in parallel with the main model
/root/venv/bin/python "$LOG/dl.py" "$MOE" > "$LOG/moe_download.log" 2>&1 &
MOE_PID=$!
/root/venv/bin/python "$LOG/dl.py" "$MODEL"
echo "[setup] $(date +%T) main weights downloaded"
df -h / | tail -n 1

nohup /root/venv/bin/vllm serve "$MODEL" --port 8000 --max-model-len 4096 \
  --gpu-memory-utilization 0.90 --max-num-seqs 64 --served-model-name "$MODEL" \
  > "$LOG/vllm.log" 2>&1 &
for _ in $(seq 1 180); do
  if curl -fs http://127.0.0.1:8000/v1/models >/dev/null 2>&1; then break; fi
  sleep 5
done
curl -fs http://127.0.0.1:8000/v1/models >/dev/null || { echo "[setup] server did not come up"; tail -n 30 "$LOG/vllm.log"; exit 11; }
wait "$MOE_PID" && echo "[setup] MoE weights ready" || echo "[setup] MoE download failed; the adaptive step will retry"
df -h / | tail -n 1
echo "[setup] $(date +%T) READY"
