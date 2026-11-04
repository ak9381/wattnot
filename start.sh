#!/usr/bin/env bash
# WattNot start-here kit. One command, no keys needed:
#   ./start.sh          set up .venv, run a 1-minute smoke check, open the dashboard on a
#                       recorded H100 run (REPLAY, MEASURED numbers), print next steps
#   ./start.sh check    set up + smoke check only (no browser, no server left running)
#   ./start.sh keys     create .env from .env.example (if missing) and show which keys are set
#   ./start.sh live     live Meta token savers on your own META_API_KEY (needs ./start.sh keys first)
# Never prints key values. Results go to out/.
set -uo pipefail
cd "$(dirname "$0")"
MODE="${1:-demo}"

say() { printf '\n== %s\n' "$*"; }

setup() {
  if [ -x .venv/bin/python ] && .venv/bin/python -c "import wattnot" 2>/dev/null; then
    echo "Environment ready (.venv)."; return 0
  fi
  say "First run: installing WattNot into .venv (about a minute)"
  bash scripts/setup.sh >/tmp/wattnot-setup.log 2>&1 \
    || { tail -20 /tmp/wattnot-setup.log; echo "setup failed (full log: /tmp/wattnot-setup.log)"; exit 1; }
  if [ -f requirements-lock.txt ]; then
    .venv/bin/python -m pip install -q -r requirements-lock.txt >>/tmp/wattnot-setup.log 2>&1 \
      || echo "note: pinned versions did not install here; using the ranges in pyproject.toml"
  fi
}

key_status() {
  # Prints only set / not set, never a value.
  for k in META_API_KEY WATTNOT_VLLM_URL BASETEN_METRICS_KEY MODAL_ENDPOINT_URL; do
    v=""; [ -f .env ] && v=$(grep -E "^${k}=" .env | tail -1 | cut -d= -f2-)
    [ -n "${!k:-}" ] && v=set
    if [ -n "$v" ]; then printf '  %-22s set\n' "$k"; else printf '  %-22s not set\n' "$k"; fi
  done
}

next_steps() {
  cat <<'TXT'

== Next steps
  1. Look around: every number on the dashboard is labelled MEASURED, ESTIMATE, SIMULATED or REPLAY.
  2. Verify a signed receipt:   .venv/bin/wattnot verify receipts/h100-1104/rcpt_20261104-032655-text.json
  3. Go live on Meta models:    ./start.sh keys   (then put your META_API_KEY in .env)
                                ./start.sh live
  4. Power caps on your own GPU + vLLM: see START-HERE.md ("Live on a GPU").
TXT
}

case "$MODE" in
  demo|check)
    setup
    say "Smoke check (no GPU, no keys)"
    .venv/bin/python -m wattnot.judge smoke || { echo "smoke check FAILED"; exit 1; }
    echo "SMOKE PASS"
    if [ "$MODE" = check ]; then next_steps; exit 0; fi
    PORT=$(.venv/bin/python -c "from wattnot.demo import find_free_port; print(find_free_port(8765))")
    URL="http://127.0.0.1:${PORT}/"
    say "Dashboard on the recorded H100 run: $URL  (Ctrl+C to stop)"
    next_steps
    ( sleep 3; command -v xdg-open >/dev/null && xdg-open "$URL" >/dev/null 2>&1 \
        || { command -v open >/dev/null && open "$URL"; } ) &
    exec .venv/bin/python -m wattnot.dash --replay-only \
      --events replays/h100-max-1103/events.jsonl --port "$PORT"
    ;;
  keys)
    if [ ! -f .env ]; then cp .env.example .env && chmod 600 .env && echo "Created .env (gitignored, mode 600)."; fi
    say "Keys (values are never shown)"
    key_status
    echo
    echo "Edit .env and fill in what you have. META_API_KEY alone is enough for ./start.sh live."
    echo "WATTNOT_MAX_SPEND caps live Meta API spend in USD."
    ;;
  live)
    setup
    if ! { [ -n "${META_API_KEY:-}" ] || grep -qE '^META_API_KEY=.+' .env 2>/dev/null; }; then
      echo "META_API_KEY is not set. Run ./start.sh keys and add it to .env."; exit 2
    fi
    exec ./run-local.sh savers
    ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
