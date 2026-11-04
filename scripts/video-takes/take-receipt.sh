. /workspace/hackathon/video/takes/helpers/typer.sh
clear; cd /tmp/wn-takes; export PYTHONPATH=src; alias wattnot='/workspace/wattnot/.venv/bin/python -m wattnot.cli'
shopt -s expand_aliases
say "Baseten round receipt (images + text), checked offline"
t "wattnot verify replays/baseten-r2/receipt.json" 3
say "Copy it, never touch the original"
t "cp replays/baseten-r2/receipt.json /tmp/tampered.json && cp replays/baseten-r2/receipt.sigstore.json /tmp/tampered.sigstore.json" 1
t "python3 /workspace/hackathon/video/takes/helpers/tamper.py /tmp/tampered.json" 2
t "wattnot verify /tmp/tampered.json" 4
t "sha256sum replays/baseten-r2/receipt.json; git status --short replays/" 4
