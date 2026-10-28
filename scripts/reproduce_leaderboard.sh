#!/usr/bin/env bash
# Rebuild the leaderboard and the proof bundle from the recorded RUN2 step
# files, then verify the signed sample receipts. No GPU, no API key, no spend.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
python -m wattnot.dash.leaderboard .
python -m wattnot.dash.judgepath .
python -m wattnot.cli verify --chain verifier/samples/chain/
python verifier/wattnot_verify.py --chain verifier/samples/chain/
echo "Compare data/leaderboard/leaderboard.json with the committed copy: git diff --stat data/"
