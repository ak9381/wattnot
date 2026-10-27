#!/usr/bin/env bash
# Build llama.cpp for the Spark Open side demo.
#   bash scripts/spark1gpu_build_llama.sh            # CUDA build if nvcc is found, else CPU
#   bash scripts/spark1gpu_build_llama.sh --cpu      # force CPU (the dry run uses this)
# Installs nothing system-wide. Output: $LLAMA_DIR/build/bin/llama-server (default /opt/llama.cpp).
# Needs git, a C++ compiler and cmake (pip install cmake ninja is enough).
set -euo pipefail
LLAMA_DIR="${LLAMA_DIR:-/opt/llama.cpp}"
MODE=auto; [ "${1:-}" = "--cpu" ] && MODE=cpu
if [ ! -d "$LLAMA_DIR/.git" ]; then
  git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
fi
cd "$LLAMA_DIR"
FLAGS=(-DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF)
if [ "$MODE" = auto ] && command -v nvcc >/dev/null 2>&1; then
  FLAGS+=(-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=90)   # H100 = sm_90
  echo "build: CUDA (sm_90)"
else
  echo "build: CPU only"
fi
cmake -S . -B build "${FLAGS[@]}"
cmake --build build --target llama-server -j "$(nproc)"
echo "built: $LLAMA_DIR/build/bin/llama-server"
echo "add it to PATH:  export PATH=$LLAMA_DIR/build/bin:\$PATH"
