#!/usr/bin/env bash
# The Oct 30 H100 kit: one script, three jobs, in this order.
#   1. probe  (<= 10 min)  can we write the power cap, does the energy counter move, is the cap put back
#   2. flex   (~30 min)    three grid shed asks (15/25/35 percent) while the load generator keeps
#                          vLLM busy; one signed flex receipt each
#   3. sweep  (core)       power-cap sweep per model, then the best cap re-run as a gated receipt
# Dry run, anywhere, no GPU (every number is SIMULATED, takes a few seconds):
#   bash scripts/h100_oct30.sh --fake
# On the GPU box (root), only after Akash has OK'd the rental:
#   export HF_TOKEN=...            # read access; Llama 3.1 licence accepted on HF
#   bash scripts/h100_oct30.sh --pre-approved [--approved-by NAME] [--gpus 2] [--flex-rps 8]
# Approve before apply: the real run changes power caps and restarts vLLM, so it first asks for
# a trial grant. --pre-approved is the explicit flag for the unattended session: the grant is
# recorded as approved by "pre-approved:h100_oct30.sh (by NAME)" in the audit trail, bounded to
# the hardware's cap range and the flex events, and every change is put back at the end.
# Without the flag the run waits (--approval-wait-s, default 600) for someone to run
# `wattnot proposals approve <id>` on the box, then exits 50 with nothing changed.
# The best settings it finds are filed as pending proposals; nothing is switched on by it.
# Proposals and the audit trail live in ${WATTNOT_PROPOSALS_DIR:-proposals} (a --fake run keeps
# them under its results folder) and a copy goes into the results as approvals/.
# Exit codes: 0 ok, 10 setup failed, 20 probe failed -> release the machine, 30 a later stage
# failed (results so far are kept), 40 ran clean but a receipt is not labelled as expected
# (a real run must be all MEASURED; a --fake run all SIMULATED), 50 nobody approved the
# session (nothing was changed).
# OPTIONAL fourth job, off by default: --adaptive-topk [--adaptive-mass 0.9]
#   4. adaptive_topk (~25 min, runs last)  adaptive top-k vs normal top-k on a small MoE model
#                          (default Granite 3.1 3B-A800M, fits one H100 80GB): joules per token
#                          from the NVML energy counter, p99, and agreement with normal top-k.
#                          A KNOWN technique (see docs/ADAPTIVE-TOPK.md); it runs through
#                          transformers, not vLLM. One signed receipt (MEASURED), and a pending
#                          proposal only if the quality, p99 and energy gates all pass.
#                          Add --adaptive-model REPO to try another Granite MoE.
#   It is time-boxed (--adaptive-budget-s, default 1500) and cannot change the exit codes
#   above: if it fails or is skipped, summary.json says so and the code stays what the other
#   jobs decided. It needs the trial grant, so --pre-approved covers it like the rest.
#   bash scripts/h100_oct30.sh --pre-approved --approved-by NAME --adaptive-topk --adaptive-mass 0.9
#   bash scripts/h100_oct30.sh --fake --adaptive-topk        # dry run, SIMULATED
# Results: results/h100-oct30-<time>/ plus results/h100-oct30-<time>.tar.gz to copy back.
# Receipts are signed with an offline test key. Nothing is published, no Rekor.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="results/h100-oct30-$(date -u +%Y%m%d-%H%M%S)"
RIG=real; EXTRA=(); PRE=0; ADAPT=0; MASS=0.9; STAGES=setup,probe,flex,sweep,best; WHO="${WATTNOT_APPROVER:-$(id -un 2>/dev/null || echo unknown)}"
while [ $# -gt 0 ]; do
  case "$1" in
    --fake) RIG=fake; EXTRA+=(--quick) ;;
    --out) OUT="$2"; shift ;;
    --gpus|--flex-rps|--gpu-gb) EXTRA+=("$1" "$2"); shift ;;
    --fail-probe) EXTRA+=("$1") ;;
    --pre-approved) PRE=1 ;;
    --approved-by) WHO="$2"; shift ;;
    --approval-wait-s) EXTRA+=("$1" "$2"); shift ;;
    --adaptive-topk) ADAPT=1 ;;
    --adaptive-mass) MASS="$2"; shift ;;
    --models|--adaptive-model|--adaptive-budget-s|--adaptive-min-agree) EXTRA+=("$1" "$2"); shift ;;
    -h|--help) sed -n '2,/^set -e/{/^set -e/!p}' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (try --help)"; exit 2 ;;
  esac
  shift
done
if ! awk -v m="$MASS" 'BEGIN { exit !(m ~ /^[0-9.]+$/ && m >= 0.5 && m < 1) }'; then
  echo "--adaptive-mass must be a number from 0.5 up to (not including) 1, got: $MASS"; exit 2
fi
if [ "$ADAPT" = 1 ]; then STAGES="$STAGES,adaptive_topk"; EXTRA+=(--adaptive-topk --adaptive-mass "$MASS"); fi
# The Python that runs the kit: $PYTHON when set (tests pass the runner that
# already has wattnot's deps), else the first `python` on PATH, else python3.
PY="${PYTHON:-python}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
if [ "$RIG" = real ]; then
  if [ ! -d .venv ]; then python3 -m venv .venv; fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PIP="python -m pip"; command -v uv >/dev/null && PIP="uv pip"
  $PIP install -q -e . "huggingface_hub[cli]>=0.30"
  if [ -z "${HF_TOKEN:-}" ] && ! printf '%s ' "${EXTRA[@]}" | grep -q -- '--models'; then
    echo "set HF_TOKEN (Llama 3.1 is gated) or pass --models muse-glimmer,qwen2.5-7b (all ungated)"; exit 10
  fi
  command -v nvidia-smi >/dev/null || { echo "no nvidia-smi: not a GPU host"; exit 10; }
  command -v docker >/dev/null || echo "warning: docker missing; Muse Glimmer needs vllm/vllm-openai:muse-glimmer"
  python -c "import vllm" 2>/dev/null || $PIP install -q vllm  # for Llama 3.1 8B
  if [ "$ADAPT" = 1 ]; then  # the adaptive top-k stage runs on transformers; a failure here only skips it
    python -c "import transformers, accelerate" 2>/dev/null || $PIP install -q transformers accelerate \
      || echo "warning: could not install transformers; the adaptive_topk stage will fail (optional)"
  fi
elif ! "$PY" -c "import wattnot" 2>/dev/null; then
  export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
fi
if [ "$RIG" = fake ]; then PROPS="$OUT/proposals"; else PROPS="${WATTNOT_PROPOSALS_DIR:-proposals}"; fi
EXTRA+=(--proposals-dir "$PROPS")
if [ "$PRE" = 1 ]; then EXTRA+=(--pre-approved "h100_oct30.sh (by $WHO)"); fi
set +e
$PY -m wattnot.runbook --rig "$RIG" --out "$OUT" --stages "$STAGES" "${EXTRA[@]}"
code=$?
set -e
want=MEASURED; [ "$RIG" = fake ] && want=SIMULATED
if [ "$code" = 0 ]; then
  $PY - "$OUT/summary.json" "$want" <<'PYEOF' || code=40
import json, sys
labels = json.load(open(sys.argv[1]))["receipt_labels"]
want = sys.argv[2]
bad = {k: v for k, v in labels.items() if k != want}
print(f"receipt labels: {labels}")
if bad or not labels:
    print(f"expected every receipt to be {want}; got {bad or 'no receipts'}")
    sys.exit(1)
PYEOF
fi
echo "results: $OUT  archive: $OUT.tar.gz  exit: $code"
[ "$code" = 20 ] && echo ">>> PROBE FAILED: release the machine now."
exit "$code"
