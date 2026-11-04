. /workspace/hackathon/video/takes/helpers/typer.sh
clear; cd /tmp; rm -rf /tmp/wattnot-fresh
say "Fresh clone, no keys in the environment"
t "git clone -q https://github.com/ak9381/wattnot /tmp/wattnot-fresh && cd /tmp/wattnot-fresh" 1
t "env | grep -ciE 'api_key|token|secret|meta_|baseten|modal_' || echo 'no keys set'" 2
t "ls .env 2>/dev/null || echo 'no .env file'" 2
t "./run-local.sh smoke" 4
echo "[exit code $?]"; sleep 4
