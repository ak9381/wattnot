. /workspace/hackathon/video/takes/helpers/typer.sh
set -a; . "$HOME/.config/wattnot/env" >/dev/null 2>&1; set +a
clear; cd /tmp/wn-takeshelp; export PYTHONPATH=src; PY=/workspace/wattnot/.venv/bin/python; H=scripts/video-takes
printf '\e[1;37;45m  LIVE  \e[0m Muse Spark, SAM 3.1 and Muse Code calls happen now\n'
printf '\e[1;37;41m  SIMULATED  \e[0m the cap surface, rack ids, GPU map and power\n'; sleep 3
say "1. Muse Spark plans the next setting"
t "\$PY \$H/spark_next.py" 4
say "2. SAM 3.1 boxes the racks; R01 waits for a human OK"
t "\$PY \$H/sam_rack_ok.py" 5
say "3. Muse Code opens a PR on a new side branch (never main)"
t "\$PY \$H/musecode_pr.py" 6
