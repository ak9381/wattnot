"""One real Muse Spark Open call on Baseten for the power-cap planner.

The trial history is made up (SIMULATED, shaped like RUN2 on an H100); the call
itself, its latency, tokens and the cap it picks are MEASURED. Writes the call
record to --out. Needs BASETEN_API_KEY in the environment; the key is never printed.

    python scripts/spark_open_planner_smoke.py --out docs/runs/spark-open-planner-smoke.json
"""

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from wattnot.proof.baseten_planner import BasetenClient, OpenWeightsPlanner

HISTORY = [  # SIMULATED trials, numbers shaped like RUN2 (Llama 3.1 8B, H100)
    {"cap_w": 400, "accepted": True, "why_not": None, "energy_change_pct": -24.0,
     "p99_change_pct": 4.1, "quality_pass_pct": 93.7, "stock_pass_pct": 93.7},
    {"cap_w": 325, "accepted": False, "why_not": "p99 +10.9% over the 10% limit",
     "energy_change_pct": -30.5, "p99_change_pct": 10.9, "quality_pass_pct": 93.7,
     "stock_pass_pct": 93.7},
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cap-usd", type=float, default=0.05)
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    client = BasetenClient(ledger_path=a.out.with_suffix(".ledger.json"), cap_usd=a.cap_usd)
    prop = OpenWeightsPlanner(client).propose(HISTORY)
    rec = {"ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
           "history": {"label": "SIMULATED", "trials": HISTORY},
           "proposal": {k: prop.get(k) for k in ("kind", "cap_w", "reason", "by")},
           "call": prop.get("call"), "tokens": prop.get("usage"),
           "usd": prop.get("usd"), "labels": {"call": "MEASURED", "usd": "ESTIMATE"}}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"proposal": rec["proposal"], "latency_ms": rec["call"]["latency_ms"],
                      "fallback": rec["call"]["fallback"], "tokens": rec["tokens"],
                      "usd": rec["usd"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
