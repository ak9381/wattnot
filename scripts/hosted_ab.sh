#!/usr/bin/env bash
# One command for the hosted with/without-WattNot A/B. Default is the CPU/mock dry-run (free).
# Live runs need Akash's okay, Modal credits confirmed, and the endpoint env set.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=${OUT:-reports/hosted-ab/mock.json}
PYTHONPATH=src ${PY:-python3} -m wattnot.spark_open.hosted_ab --mock --out "$OUT" "$@"
