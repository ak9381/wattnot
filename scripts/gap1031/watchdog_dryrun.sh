#!/usr/bin/env bash
# Gap test 8: all five watchdog layers, dry-run, fake VM id, no E2E credentials in the env.
# Runs every layer under `env -i` so no key can leak in; E2E_VM_ID is a fake id (999999).
set -u
ROOT=$(cd "$(dirname "$0")/../.." && pwd); OUT=$ROOT/data/gap-1031/watchdog; mkdir -p "$OUT"
T=$(mktemp -d); NOW=$(date +%s)
mk(){ printf '{"ts": %s, "launch_ts": %s, "deadline_ts": %s, "spent_inr": %s, "block": "fake", "note": "gap-1031 dry-run"}\n' "$1" "$((NOW-600))" "$2" "$3" > "$T/$4.json"; }
mk $NOW $((NOW+3600)) 100 fresh
mk $((NOW-1800)) $((NOW+3600)) 100 stale
mk $NOW $((NOW+3600)) 9999 overbudget
mk $NOW $((NOW-60)) 100 pastdeadline
# stand-in for the layer 2/3 agent CLI: runs the exact command from the prompt (not an AI agent)
cat > "$T/agent.sh" <<'A'
#!/usr/bin/env bash
cmd=$(sed -n '2s/^ *1\. Run this command verbatim://p;3p' | tail -1); eval "$cmd"
A
chmod +x "$T/agent.sh"
E="env -i PATH=/usr/bin:/bin HOME=$T PYTHONPATH=$ROOT/src E2E_VM_ID=999999"
for s in fresh stale overbudget pastdeadline; do
  hb=$T/$s.json; A="--budget-cap-inr 2000 --max-hours 3.6"
  echo "== $s"
  echo "-- check";   $E python3 -m wattnot.longrun.watchdog check --heartbeat $hb $A --dry-run 2>&1 | tail -1
  echo "-- layer1";  $E timeout 20 python3 -m wattnot.longrun.watchdog.local --heartbeat $hb $A --dry-run --poll-every-s 5 2>&1 | tail -1
  for L in 2 3; do echo "-- layer$L"; $E WATTNOT_WD${L}_CMD=$T/agent.sh timeout 20 python3 -m wattnot.longrun.watchdog.agent --layer $L --heartbeat $hb $A --interval-s 5 --dry-run 2>&1 | tail -1; done
  echo "-- layer4";  $E timeout 30 python3 -m wattnot.longrun.watchdog.offbox --heartbeat-source $hb $A 2>&1 | tail -1
  echo "-- layer5";  $E timeout 30 python3 $ROOT/scripts/mac_watchdog/watchdog.py --heartbeat-source $hb $A --vm-id 999999 --once --dry-run 2>&1 | tail -1
done > "$OUT/run.log" 2>&1
rm -rf "$T"; cat "$OUT/run.log"
