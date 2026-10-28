#!/usr/bin/env bash
# Create the Modal Endpoint WattNot's hosted Modal placement uses.
#
# Dry run by default: prints the plan and runs only free, read-only checks.
# Creating an endpoint can cost money, so it needs --yes.
#
#   bash scripts/modal_deploy.sh                 # check auth, list endpoints, print the plan
#   bash scripts/modal_deploy.sh --yes           # Shared Muse Spark Open (billed per token)
#   bash scripts/modal_deploy.sh --dedicated --yes   # Dedicated Muse Glimmer 30B (billed GPU time)
#
# The CLI uses the account token (MODAL_TOKEN_ID / MODAL_TOKEN_SECRET, or ~/.modal.toml).
# Calls to the endpoint use a separate Proxy Token (MODAL_PROXY_TOKEN_ID / _SECRET).
# Nothing here prints a secret.
set -euo pipefail

YES=0; DEDICATED=0; NAME=wattnot-spark-open
for a in "$@"; do
  case "$a" in
    --yes) YES=1 ;;
    --dedicated) DEDICATED=1; NAME=wattnot-glimmer ;;
    *) echo "unknown flag: $a"; exit 2 ;;
  esac
done

if [ "$DEDICATED" = 1 ]; then
  MODEL=meta-models/Muse-Glimmer-30B; MODE_FLAGS=""
  COST="GPU time while a container is up (H100 list \$3.95/h; scales to zero when idle)"
else
  MODEL=meta-models/Muse-Spark-Open-816B-A42B; MODE_FLAGS="--shared"
  COST="per token: \$1.25 in / \$0.15 cached / \$4.25 out per 1M (no idle cost)"
fi

command -v modal >/dev/null || { echo "modal CLI not found: pip install modal"; exit 1; }
echo "== auth (free)"
modal token info 2>&1 | grep -E "^(Workspace|User):" || { echo "not signed in to Modal"; exit 1; }
echo "== endpoints now (free)"
modal endpoint list
echo "== plan"
echo "  modal endpoint create $MODE_FLAGS --name $NAME --model $MODEL"
echo "  cost: $COST"
if [ "$YES" != 1 ]; then
  echo "dry run: nothing created. Re-run with --yes once the credits are on the account."
  exit 0
fi
modal endpoint create $MODE_FLAGS --name "$NAME" --model "$MODEL"
echo "== next"
echo "  modal endpoint info $NAME                      # copy the URL"
echo "  modal workspace proxy-tokens create            # once; keep the id and secret"
echo "  export MODAL_ENDPOINT_URL=<url> MODAL_PROXY_TOKEN_ID=wk-... MODAL_PROXY_TOKEN_SECRET=ws-..."
echo "  export MODAL_MODEL=$MODEL   # or the name GET \$MODAL_ENDPOINT_URL/v1/models returns"
