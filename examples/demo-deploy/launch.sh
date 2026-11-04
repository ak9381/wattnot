#!/usr/bin/env bash
set -euo pipefail

# WattNot Demo Deploy: vLLM service launch
# Applied settings from optimization runs

# Power cap limit
# sudo nvidia-smi -pl 400

exec vllm serve meta-models/Muse-Glimmer-30B \
    --host 0.0.0.0 \
    --port 8000 \
    --max-num-seqs 256 \
    --gpu-memory-utilization 0.90
