#!/usr/bin/env python3
"""Re-score saved eye_test.json files with the standard keep rule (no rerun, no GPU)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wattnot.vision.gate import no_worse_than_stock

for f in sys.argv[1:]:
    p = Path(f); d = json.loads(p.read_text())
    v = no_worse_than_stock(d.get("energy_arms"))
    if v is None:
        print(f"{p}: one arm only, left as is"); continue
    d.setdefault("strict_all_correct", d.get("passed")); d["gate"] = v; d["passed"] = v["keep"]
    d["rescored"] = "re-scored from saved results with no_worse_than_stock (no rerun)"
    p.write_text(json.dumps(d, indent=2) + "\n")
    print(f"{p}: {'KEEP' if v['keep'] else 'REJECT'} WattNot {v['with_wattnot']} vs stock {v['stock']}")
