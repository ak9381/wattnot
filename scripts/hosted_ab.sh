#!/usr/bin/env bash
# One command for the hosted with/without-WattNot A/B. Default is the CPU/mock dry-run (free).
# Live (paid): MODE=--live scripts/hosted_ab.sh --hosts baseten  (needs Akash's okay; $5 hard stop)
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=${OUT:-reports/hosted-ab/mock.json}
PYTHONPATH=src ${PY:-python3} -m wattnot.spark_open.hosted_ab ${MODE:---mock} --out "$OUT" "$@"
