#!/usr/bin/env bash
# Serve Muse Glimmer 30B (Meta) on one H100 with the settings RUN1 used, in BF16 or FP8.
#   bash scripts/glimmer_serve.sh [bf16|fp8] [port]
# Stops any running server first (vLLM or the Glimmer container). Weights come from the HF
# cache that h100_vm_setup.sh filled; FP8 is quantized on load, so there is no second download.
# Needs docker + the NVIDIA container toolkit (RUN1 used vllm/vllm-openai:muse-glimmer).
set -euo pipefail
PREC="${1:-bf16}"; PORT="${2:-8000}"
MODEL=meta-models/Muse-Glimmer-30B
IMAGE="${GLIMMER_IMAGE:-vllm/vllm-openai:muse-glimmer}"
LOG="${LOG:-/root/run2/logs}"; mkdir -p "$LOG"
command -v docker >/dev/null || { echo "[glimmer] docker missing: cannot serve Glimmer"; exit 21; }
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull -q "$IMAGE" >/dev/null
pkill -f "vllm serve" 2>/dev/null || true
docker rm -f glimmer >/dev/null 2>&1 || true
sleep 5
EXTRA=()
if [ "$PREC" = fp8 ]; then EXTRA+=(--quantization fp8); fi
# e.g. GLIMMER_EXTRA_ARGS="--enable-prefix-caching" (prefix_cache_ab.py sets it per arm)
# shellcheck disable=SC2206
if [ -n "${GLIMMER_EXTRA_ARGS:-}" ]; then EXTRA+=(${GLIMMER_EXTRA_ARGS}); fi
docker run -d --name glimmer --gpus device=0 --ipc=host -p "$PORT:$PORT" \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" -e HF_TOKEN \
  "$IMAGE" "$MODEL" --port "$PORT" --served-model-name "$MODEL" \
  --reasoning-parser muse_glimmer --max-model-len 8192 \
  --gpu-memory-utilization 0.90 --max-num-seqs 64 "${EXTRA[@]}" > /dev/null
docker logs -f glimmer > "$LOG/vllm-glimmer-$PREC.log" 2>&1 &
for _ in $(seq 1 240); do
  curl -fs "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1 && break
  # fail fast if the server died (bad flag, OOM): don't burn 20 min of GPU waiting
  if [ "$(docker inspect -f '{{.State.Running}}' glimmer 2>/dev/null)" != "true" ]; then break; fi
  sleep 5
done
curl -fs "http://127.0.0.1:$PORT/v1/models" >/dev/null || { echo "[glimmer] $PREC did not come up"; tail -n 30 "$LOG/vllm-glimmer-$PREC.log"; exit 22; }
# the numbers the shakedown needs, straight from the vLLM log
grep -E "Model loading took|Available KV cache memory|GPU KV cache size|init engine" \
  "$LOG/vllm-glimmer-$PREC.log" | sed "s/^/[glimmer $PREC] /"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader | sed "s/^/[glimmer $PREC] mem /"
echo "[glimmer] $PREC READY on :$PORT"
