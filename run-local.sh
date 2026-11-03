#!/usr/bin/env bash
# WattNot for judges. One command:
#   ./run-local.sh            # doctor, then the best mode your machine supports
#   ./run-local.sh doctor     # checks only, changes nothing
#   ./run-local.sh gpu        # live: power caps + quality gate on your GPU + vLLM (needs root)
#   ./run-local.sh savers     # live: token savers on the Meta API, no GPU needed
#   ./run-local.sh smoke      # 1-minute end-to-end check, no GPU, no key
#   ./run-local.sh sim        # SIMULATED: no GPU, no key, every number labelled MOCK
#   ./run-local.sh replay [h100-max-1103|modal-r1|modal-r2|baseten-r1|baseten-r2]
#                             # dashboard on our recorded runs (MEASURED replay)
# Settings come from .env (copy .env.example). Results go to out/judge-<time>/.
set -uo pipefail
cd "$(dirname "$0")"
MODE="${1:-auto}"; shift || true
# .env holds your settings (copy .env.example). Placeholders count as unset.
if [ -f .env ]; then set -a; . ./.env; set +a; fi
case "${META_API_KEY:-}" in your-*) META_API_KEY="";; esac; export META_API_KEY
OUT="out/judge-$(date -u +%Y%m%d-%H%M%S)"
say() { printf '\n== %s\n' "$*"; }

# venv + install, once
if ! { [ -x .venv/bin/python ] && .venv/bin/python -c "import wattnot" 2>/dev/null; }; then
  echo "First run: installing WattNot into .venv (about a minute)..."
  bash scripts/setup.sh >/tmp/wattnot-setup.log 2>&1 \
    || { tail -20 /tmp/wattnot-setup.log; echo "setup failed (full log: /tmp/wattnot-setup.log)"; exit 1; }
  if [ -f requirements-lock.txt ]; then
    .venv/bin/python -m pip install -q -r requirements-lock.txt \
      || echo "note: pinned versions did not install here; using the ranges in pyproject.toml"
  fi
fi
# shellcheck disable=SC1091
. .venv/bin/activate
PY=python

doctor_mode() { $PY -m wattnot.cli doctor --json | $PY -c 'import json,sys; print(json.load(sys.stdin)["mode"])'; }

# Belt and braces on top of the harness's own CapGuard: put the stock limit back on any exit.
STOCK_W=""
restore_cap() {
  if [ "${WATTNOT_RESTORE_CAP:-1}" = 1 ] && [ -n "$STOCK_W" ] && command -v nvidia-smi >/dev/null; then
    nvidia-smi -i 0 -pl "$STOCK_W" >/dev/null 2>&1 \
      && echo "power limit restored to stock ${STOCK_W} W" \
      || echo "WARNING: could not restore ${STOCK_W} W; run: sudo nvidia-smi -i 0 -pl ${STOCK_W}"
  fi
}

run_gpu() {
  : "${WATTNOT_VLLM_URL:?set WATTNOT_VLLM_URL in .env (your vLLM chat URL)}"
  STOCK_W=$(nvidia-smi -i 0 --query-gpu=power.default_limit --format=csv,noheader,nounits 2>/dev/null | cut -d. -f1)
  trap restore_cap EXIT INT TERM
  mkdir -p "$OUT"
  say "GPU run: stock vs ${WATTNOT_CAP_W:-375} W on 4 prompt tiers, quality gate on (MEASURED via NVML)"
  $PY -m wattnot.longrun.harness --cap "${WATTNOT_CAP_W:-375}" --url "$WATTNOT_VLLM_URL" \
      --model-config "${WATTNOT_MODEL_CONFIG:-muse-glimmer-30b}" --per-tier "${WATTNOT_PER_TIER:-5}" \
      --out "$OUT/gpu" || echo "GPU step failed (see above); the power limit is restored on exit"
  if [ -n "${META_API_KEY:-}" ]; then run_savers; fi
}

run_savers() {
  : "${META_API_KEY:?set META_API_KEY in .env}"
  mkdir -p "$OUT/savers"
  LEDGER="$OUT/savers/meta_spend.json"
  say "Key check: one tiny Meta API call"
  $PY -m wattnot.cli meta check --ledger "$LEDGER" || { echo "key check failed: check META_API_KEY"; return 1; }
  say "Cheapest-first routing with step-up and thinking budget (live Meta API, MEASURED tokens/latency/cost)"
  $PY -m wattnot.cli meta session --steps 3 --ledger "$LEDGER" --out "$OUT/savers/session.json" \
    --cache-dir "$OUT/savers/cache" || echo "session step failed; continuing"
  say "Agentwaste proxy + context saver on agent tasks (live, capped at \$${WATTNOT_MAX_SPEND:-0.50})"
  WATTNOT_AW_CEILING="${WATTNOT_MAX_SPEND:-0.50}" $PY scripts/agentwaste_v3_bench.py --reps 1 \
    --max-spend "${WATTNOT_MAX_SPEND:-0.50}" --out "$OUT/savers/agentwaste" || echo "agentwaste step failed; continuing"
  $PY -m wattnot.cli meta spend --ledger "$LEDGER"
  echo "Energy for hosted API calls is an ESTIMATE (no GPU meter on a hosted API)."
}

run_sim() {
  mkdir -p "$OUT"
  say "SIMULATED run: fake GPU + oracle model. Every number is MOCK."
  $PY -m wattnot.longrun.harness --fake --cap 375 --per-tier 3 --out "$OUT/sim"
  echo "Open the dashboard on a recorded real run with: ./run-local.sh replay"
}

run_replay() {
  NAME="${1:-h100-max-1103}"
  EV="replays/$NAME/events.jsonl"
  [ -f "$EV" ] || { echo "no replay '$NAME'. Try: $(ls replays | grep -v INDEX | tr '\n' ' ')"; exit 1; }
  say "MEASURED replay of $NAME (recorded run, nothing live). Ctrl-C to stop."
  PORT=$($PY -c "from wattnot.demo import find_free_port; print(find_free_port(${WATTNOT_PORT:-8765}))")
  [ "$PORT" != "${WATTNOT_PORT:-8765}" ] && echo "port ${WATTNOT_PORT:-8765} is busy, using $PORT"
  echo "Open: http://127.0.0.1:$PORT/"
  exec $PY -m wattnot.dash --replay-only --events "$EV" --port "$PORT"
}

run_smoke() {
  say "Smoke test: doctor, simulated run, replay server up and down (no GPU, no key, no spend)"
  $PY -m wattnot.cli doctor >/dev/null || { echo "SMOKE FAIL: doctor"; exit 1; }
  run_sim >/dev/null || { echo "SMOKE FAIL: simulated run"; exit 1; }
  [ -s "$OUT/sim/tiers_summary.json" ] || { echo "SMOKE FAIL: no sim summary"; exit 1; }
  PORT=$($PY -c "from wattnot.demo import find_free_port; print(find_free_port(${WATTNOT_PORT:-8765}))")
  $PY -m wattnot.dash --replay-only --events replays/h100-max-1103/events.jsonl --port "$PORT" >"$OUT/dash.log" 2>&1 &
  DPID=$!; trap 'kill $DPID 2>/dev/null' EXIT
  ok=0; for _ in $(seq 1 30); do sleep 1; $PY - "$PORT" <<'PYEOF' && { ok=1; break; }
import sys, urllib.request
urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/", timeout=2).read()
PYEOF
  done
  kill $DPID 2>/dev/null; wait $DPID 2>/dev/null
  [ $ok = 1 ] || { echo "SMOKE FAIL: dashboard did not answer (see $OUT/dash.log)"; exit 1; }
  echo "SMOKE PASS"
}

case "$MODE" in
  doctor) $PY -m wattnot.cli doctor ;;
  auto)
    $PY -m wattnot.cli doctor || exit $?
    m=$(doctor_mode)
    if [ "$m" = full ]; then run_gpu
    elif [ -n "${META_API_KEY:-}" ]; then run_savers
    else echo "No META_API_KEY: showing our recorded real runs instead (MEASURED replay)."; run_replay; fi ;;
  gpu) $PY -m wattnot.cli doctor
       if [ "$(doctor_mode)" != full ]; then echo "power caps are off on this machine (see doctor above); running the token savers instead"
         if [ -n "${META_API_KEY:-}" ]; then run_savers; else run_replay; fi
       else run_gpu; fi ;;
  savers) run_savers ;;
  sim) run_sim ;;
  smoke) run_smoke ;;
  replay) run_replay "$@" ;;
  *) sed -n 2,11p "$0"; exit 1 ;;
esac
echo; echo "Results: $OUT/"
