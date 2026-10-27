"""Build data/dash/story.json from recorded runs. Read-only on every input."""
import argparse
import json
from pathlib import Path

from wattnot.dash import arch

ap = argparse.ArgumentParser()
ap.add_argument("--e2e", default="/workspace/hackathon/e2e-run1/results", type=Path)
ap.add_argument("--ledger", default=None, type=Path)
ap.add_argument("--out", default=arch.ROOT / "data" / "dash" / "story.json", type=Path)
a = ap.parse_args()
b = arch.build(e2e=a.e2e, ledger=a.ledger)
a.out.parent.mkdir(parents=True, exist_ok=True)
a.out.write_text(json.dumps(b, separators=(",", ":"), ensure_ascii=False))
kb = a.out.stat().st_size // 1024
print(f"wrote {a.out} ({kb} KB), nodes {len(b['nodes'])}, replays {list(b['replays'])}")
