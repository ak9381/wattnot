#!/usr/bin/env bash
# WattNot one-command kit for macOS and Linux (Windows: start.cmd / start.ps1).
#   ./start.sh            preflight -> install -> compatibility check -> dashboard (REPLAY) -> browser
#   ./start.sh replay     same as above (default; no keys, no GPU, recorded MEASURED H100 run)
#   ./start.sh check      preflight + install + compatibility check only, nothing left running
#   ./start.sh keys       enter API keys (hidden input) into .env (mode 600); shows set / not set only
#   ./start.sh live       live token savers on Meta models        (META_API_KEY)
#   ./start.sh baseten    reasoning-effort tuning on Baseten      (BASETEN_METRICS_KEY)
#   ./start.sh modal      reasoning-effort tuning on a Modal app  (WATTNOT_MODAL_URL + MODAL_PROXY_TOKEN_*)
#   ./start.sh gpu        power caps on your own NVIDIA GPU + vLLM (WATTNOT_VLLM_URL, root)
# Docker is not needed. Key values are never printed. Help: GETTING-STARTED.md
set -uo pipefail
cd "$(dirname "$0")"
MODE="${1:-replay}"
GUIDE="GETTING-STARTED.md#troubleshooting"
PY=.venv/bin/python
ASSUME_YES="${WATTNOT_YES:-0}"

say()  { printf '\n== %s\n' "$*"; }
fail() { # fail CODE "message"
  printf '\nERROR %s: %s\n  Fix: see %s (entry %s)\n' "$1" "$2" "$GUIDE" "$1" >&2; exit "${3:-1}"; }
row()  { printf '  %-26s %-8s %s\n' "$1" "$2" "$3"; }

# ---------- .env loading (KEY=VALUE only, never eval'd, placeholders ignored) ----------
load_env() {
  [ -f .env ] || return 0
  local line k v
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    k=${line%%=*}; v=${line#*=}
    [[ "$k" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
    v=${v%%[[:space:]]#*}; v=${v%"${v##*[![:space:]]}"}
    case "$v" in ''|your-*-here) continue ;; esac
    [ -z "${!k:-}" ] && export "$k=$v"
  done < .env
}
is_set() { [ -n "${!1:-}" ]; }

# ---------- 1) PREFLIGHT ----------
pick_python() {
  for c in python3.13 python3.12 python3.11 python3 python; do
    command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; raise SystemExit(sys.version_info[:2] < (3, 11))' 2>/dev/null \
      && { echo "$c"; return 0; }
  done; return 1
}
MISSING=""
preflight() {
  say "1/5 Preflight: what this machine has"
  row CHECK STATUS DETAIL; row ----- ------ ------
  local p; MISSING=""
  if p=$(pick_python); then row "Python >= 3.11" OK "$($p -V 2>&1) ($p)"; BASEPY=$p
  else row "Python >= 3.11" MISSING "found: $(python3 -V 2>&1 || echo none)"; MISSING="$MISSING python"; BASEPY=""; fi
  if [ -n "$BASEPY" ] && $BASEPY -c 'import venv, ensurepip' 2>/dev/null; then row "pip / venv" OK "ensurepip + venv present"
  else row "pip / venv" MISSING "python venv module"; MISSING="$MISSING venv"; fi
  if command -v git >/dev/null; then row git OK "$(git --version | cut -d' ' -f3)"; else row git MISSING ""; MISSING="$MISSING git"; fi
  if command -v curl >/dev/null; then row curl OK ""; else row curl MISSING ""; MISSING="$MISSING curl"; fi
  local free; free=$(df -Pk . | awk 'NR==2{print int($4/1048576)}')
  if [ "${free:-0}" -ge 2 ]; then row "free disk" OK "${free} GB (need 2)"; else row "free disk" LOW "${free} GB (need 2)"; MISSING="$MISSING disk"; fi
  PORT="${WATTNOT_PORT:-8765}"
  if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then row "port $PORT" BUSY "another free port will be picked"
  else row "port $PORT" OK "free"; fi
  if [ -x "$PY" ] && $PY -c 'import wattnot' 2>/dev/null; then row "PyPI network" SKIP "already installed"
  elif command -v curl >/dev/null && curl -fsS -m 8 -o /dev/null https://pypi.org/simple/pip/; then row "PyPI network" OK "pypi.org reachable"
  else row "PyPI network" FAIL "pypi.org not reachable (proxy/SSL?)"; MISSING="$MISSING network"; fi
  row "Docker" "N/A" "not needed"
}

# ---------- 2) INSTALL ----------
ask() { [ "$ASSUME_YES" = 1 ] && return 0; [ -t 0 ] || return 1; read -r -p "$1 [y/N] " a; [[ "$a" =~ ^[Yy]$ ]]; }
install_system() {
  local need="" m
  for m in $MISSING; do case $m in python|venv|git|curl) need="$need $m";; esac; done
  need=${need# }
  for m in $MISSING; do
    [ "$m" = disk ] && fail E05 "less than 2 GB free disk here"
    [ "$m" = network ] && fail E06 "cannot reach pypi.org to download packages"
  done
  [ -z "$need" ] && return 0
  say "2/5 Install: missing $need"
  case "$(uname -s)" in
    Darwin)
      command -v brew >/dev/null || { echo '  Install Homebrew first (one line), then re-run ./start.sh:'
        echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
        fail E02 "Homebrew is missing"; }
      ask "  Run: brew install python@3.12 git ?" || fail E01 "missing $need; install declined"
      brew install python@3.12 git || fail E01 "brew install failed" ;;
    Linux)
      if command -v apt-get >/dev/null; then echo "  Run:  sudo apt-get update && sudo apt-get install -y python3.12 python3.12-venv git curl"
      elif command -v dnf >/dev/null; then echo "  Run:  sudo dnf install -y python3.12 git curl"
      else echo "  Install Python 3.11+, git and curl with your package manager."; fi
      fail E01 "missing $need; run the command above, then ./start.sh again" ;;
    *) fail E01 "missing $need" ;;
  esac
  preflight
}
install_wattnot() {
  if [ -x "$PY" ] && $PY -c 'import wattnot' 2>/dev/null; then echo "  WattNot already installed in .venv."; return 0; fi
  say "2/5 Install: .venv + WattNot from requirements-lock.txt (about a minute)"
  local log=/tmp/wattnot-setup.log
  { $BASEPY -m venv .venv && $PY -m pip install -q --upgrade pip \
    && $PY -m pip install -q -r requirements-lock.txt && $PY -m pip install -q --no-deps -e . ; } >"$log" 2>&1 \
    || { tail -15 "$log"; fail E07 "pip install failed (full log: $log)"; }
  echo "  Installed."
}

# ---------- 3) COMPATIBILITY ----------
compat() {
  say "3/5 Compatibility check"
  local os; case "$(uname -s)" in Darwin) os="macOS $(sw_vers -productVersion 2>/dev/null)";;
    Linux) os="Linux $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-}")";; *) os=$(uname -s);; esac
  row OS OK "$os"
  local arch; arch=$(uname -m); row "CPU arch" OK "$arch"
  if [ "$(uname -s)" = Darwin ] && [ "$arch" = x86_64 ] && sysctl -n sysctl.proc_translated 2>/dev/null | grep -q 1; then
    row "Rosetta" WARN "x86_64 shell on Apple Silicon; arm64 is faster"; fi
  row Python OK "$($PY -V 2>&1)"
  $PY -c 'import wattnot' 2>/dev/null && row "import wattnot" OK "" || fail E08 "import wattnot failed in .venv"
  if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then row "NVIDIA GPU" INFO "$(nvidia-smi -L | head -1)"
  else row "NVIDIA GPU" INFO "none (fine: only gpu mode needs one)"; fi
  echo "  Smoke test (no GPU, no keys, about a minute)..."
  $PY -m wattnot.judge smoke >/tmp/wattnot-smoke.log 2>&1 || { tail -15 /tmp/wattnot-smoke.log; fail E09 "smoke test failed"; }
  echo "SMOKE PASS"
}

prepare() { load_env; preflight; install_system; install_wattnot; compat; }

next_steps() {
  cat <<'TXT'

== Next steps
  Verify a signed receipt:  .venv/bin/wattnot verify receipts/h100-1104/rcpt_20261104-000651-text.json
  Add your own keys:        ./start.sh keys
  Live modes:               ./start.sh live | baseten | modal | gpu
  Full guide:               GETTING-STARTED.md
TXT
}

open_url() { if command -v open >/dev/null && [ "$(uname -s)" = Darwin ]; then open "$1"
  elif command -v xdg-open >/dev/null; then xdg-open "$1" >/dev/null 2>&1; else echo "  Open $1 in your browser."; fi; }

replay() {
  prepare
  PORT=$($PY -c "from wattnot.demo import find_free_port; print(find_free_port(${WATTNOT_PORT:-8765}))")
  URL="http://127.0.0.1:${PORT}/"
  say "4/5 Start: dashboard on the recorded H100 run (REPLAY of MEASURED numbers)"
  $PY -m wattnot.dash --replay-only --events replays/h100-max-1103/events.jsonl --port "$PORT" >/tmp/wattnot-dash.log 2>&1 &
  DASH=$!; trap 'kill $DASH 2>/dev/null' INT TERM EXIT
  for _ in $(seq 1 60); do
    curl -fsS -m 2 "http://127.0.0.1:${PORT}/api/dash/health" >/dev/null 2>&1 && break
    kill -0 $DASH 2>/dev/null || { tail -15 /tmp/wattnot-dash.log; fail E10 "dashboard exited during start"; }
    sleep 1
  done
  curl -fsS -m 2 "http://127.0.0.1:${PORT}/api/dash/health" >/dev/null 2>&1 || fail E10 "dashboard health check timed out"
  echo "  Healthy: ${URL}api/dash/health"
  say "5/5 Open: $URL  (Ctrl+C to stop)"
  [ "${WATTNOT_NO_BROWSER:-0}" = 1 ] || open_url "$URL"
  next_steps
  wait $DASH
}

# ---------- keys ----------
KEYS="META_API_KEY BASETEN_METRICS_KEY WATTNOT_MODAL_URL MODAL_PROXY_TOKEN_ID MODAL_PROXY_TOKEN_SECRET WATTNOT_VLLM_URL WATTNOT_MAX_SPEND"
key_mode() { case $1 in META_API_KEY) echo live;; BASETEN_METRICS_KEY) echo baseten;; WATTNOT_MODAL_URL|MODAL_PROXY_TOKEN_*) echo modal;;
  WATTNOT_VLLM_URL) echo gpu;; WATTNOT_MAX_SPEND) echo "live (USD cap)";; esac; }
key_table() {
  row KEY STATUS "NEEDED FOR"; row --- ------ ----------
  for k in $KEYS; do if is_set "$k"; then s=set; else s="not set"; fi; row "$k" "$s" "$(key_mode "$k")"; done
  row "(none)" "-" "replay, check"
}
set_env_line() { # set_env_line KEY VALUE ; value never echoed
  local tmp; tmp=$(mktemp); grep -v -E "^$1=" .env > "$tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$tmp"; cat "$tmp" > .env; rm -f "$tmp"; }
keys() {
  [ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example (gitignored)."; }
  chmod 600 .env
  load_env
  say "Keys (values are never shown)"; key_table
  [ -t 0 ] || { echo; echo "Not a terminal: edit .env by hand to add keys."; return 0; }
  echo; echo "Press Enter to skip a key and keep what is there. Input is hidden."
  for k in $KEYS; do
    read -r -s -p "  $k ($(key_mode "$k")): " v; echo
    [ -n "$v" ] && { set_env_line "$k" "$v"; export "$k=$v"; }
  done
  unset v; chmod 600 .env
  say "Saved to .env (mode 600)"; key_table
}

need() { # need MODE VAR...
  local m=$1; shift; local miss=""
  for k in "$@"; do is_set "$k" || miss="$miss $k"; done
  miss=${miss# }; [ -z "$miss" ] || fail E11 "$m mode needs $miss (run ./start.sh keys)" 2
}

case "$MODE" in
  replay|demo) replay ;;
  check) prepare; next_steps ;;
  keys) keys ;;
  live) load_env; need live META_API_KEY; prepare
        say "Live token savers on Meta models (spend cap WATTNOT_MAX_SPEND=${WATTNOT_MAX_SPEND:-0.50} USD)"
        exec ./run-local.sh savers ;;
  baseten) load_env; need baseten BASETEN_METRICS_KEY; prepare
        say "Reasoning-effort tuning on Baseten (your key; budget ${WATTNOT_MAX_SPEND:-0.50} USD)"
        exec .venv/bin/wattnot cloud tune --live --budget-usd "${WATTNOT_MAX_SPEND:-0.50}" ;;
  modal) load_env
        is_set WATTNOT_MODAL_URL || is_set MODAL_ENDPOINT_URL || fail E11 "modal mode needs WATTNOT_MODAL_URL (run ./start.sh keys)" 2
        need modal MODAL_PROXY_TOKEN_ID MODAL_PROXY_TOKEN_SECRET; prepare
        say "Reasoning-effort tuning on your Modal endpoint"
        exec .venv/bin/wattnot cloud tune --provider modal --live --budget-usd "${WATTNOT_MAX_SPEND:-0.50}" ;;
  gpu) load_env; prepare
        command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1 \
          || { ./run-local.sh doctor || true; fail E12 "gpu mode needs an NVIDIA GPU with a working nvidia-smi; none found here" 3; }
        need gpu WATTNOT_VLLM_URL
        curl -fsS -m 5 -o /dev/null "${WATTNOT_VLLM_URL%/chat/completions}/models" || fail E13 "vLLM not reachable at WATTNOT_VLLM_URL"
        exec ./run-local.sh gpu ;;
  -h|--help|help) sed -n '2,11p' "$0" ;;
  *) sed -n '2,11p' "$0"; fail E00 "unknown command '$MODE'" 2 ;;
esac
