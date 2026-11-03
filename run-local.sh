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

# Everything after setup lives in one cross-platform entry point (also used on Windows).
exec python -m wattnot.judge "$MODE" "$@"
