. /workspace/hackathon/video/takes/helpers/typer.sh
clear; cd /tmp/wn-takes; export PYTHONPATH=src; PY=/workspace/wattnot/.venv/bin/python
printf '\e[1;37;41m  SIMULATED  \e[0m rack inventory, slot map, rack budget (rack/audit/fleet.json)\n'
printf '\e[1;30;42m  MEASURED   \e[0m SAM 3.1 unit boxes, replayed from a saved live call (rack/*/sam-raw)\n'
sleep 3
say "SAM boxes vs the (SIMULATED) inventory"
t "grep -m1 '\"label\"' rack/audit/fleet.json" 2
t "\$PY scripts/sam_audit.py" 4
say "Ask for a cap on a GPU SAM can't see"
t "\$PY /workspace/hackathon/video/takes/helpers/cap_request.py 12 350" 3
say "Same ask on a GPU SAM did box"
t "\$PY /workspace/hackathon/video/takes/helpers/cap_request.py 8 350" 3
say "The boxes (photo MEASURED, inventory SIMULATED)"
sleep 1
google-chrome --user-data-dir=/tmp/chr8 --no-first-run --kiosk --window-size=1920,1080 --window-position=0,0 "file:///workspace/hackathon/video/takes/helpers/sam-view.html" >/dev/null 2>&1 &
CP=$!; sleep 9; kill $CP; sleep 1
