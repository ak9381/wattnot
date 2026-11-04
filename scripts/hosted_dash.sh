#!/usr/bin/env bash
# Dashboard for Go live on a hosted provider. One server: stop it before switching provider.
#   scripts/hosted_dash.sh modal     # Muse Glimmer 30B on Modal A100, NVML energy MEASURED, no power cap
#   scripts/hosted_dash.sh baseten   # Muse Spark Open on Baseten's Model API, energy ESTIMATE, no power cap
set -euo pipefail
cd "$(dirname "$0")/.."
P=${1:?modal or baseten}; PORT=${PORT:-8772}
case "$P" in
  modal)   CFG=configs/gw-modal-remote.yaml; set -a; . "$HOME/.config/wattnot/modal.env"; set +a ;;
  baseten) CFG=configs/gw-baseten.yaml; [ -f "$HOME/.config/wattnot/baseten-cli.env" ] && . "$HOME/.config/wattnot/baseten-cli.env"
           : "${BASETEN_METRICS_KEY:?BASETEN_METRICS_KEY missing}" ;;
  *) echo "modal or baseten"; exit 2 ;;
esac
set -a; . "$HOME/.config/wattnot/env" >/dev/null 2>&1 || true; set +a   # META_API_KEY for voice transcription
OUT=out/hosted/$P-ui; mkdir -p "$OUT"
export PYTHONPATH=src WATTNOT_SAVINGS_LOG=$OUT/savings.jsonl WATTNOT_NODE_LOG=$OUT/node.jsonl \
  WATTNOT_PLUG_YAML=$OUT/plug.yaml WATTNOT_SEQ_MAX_USD=${WATTNOT_SEQ_MAX_USD:-5}
echo "WattNot on $P: http://127.0.0.1:$PORT/graph.html  (logs in $OUT, hard stop \$$WATTNOT_SEQ_MAX_USD per run)"
exec "${PY:-/tmp/wn-venv/bin/python}" -m wattnot.dash --port "$PORT" --gateway "$CFG"
