"""Record one live Muse Spark `propose` call (numbers only) and run the ablation.

    META_API_KEY=... python scripts/record_proposals.py

Writes tests/fixtures/spark_plan_recorded.json and reports/ablation.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from wattnot.planner.ablation import ablation
from wattnot.planner.optuna_study import Bounds, PlannerContext, propose
from wattnot.planner.spark import SparkClient

ROOT = Path(__file__).resolve().parents[1]
CTX = PlannerContext(Bounds(), base_wh_per_req=0.048, base_p99_s=1.0, p99_budget_s=1.3)
USD_PER_CALL = 0.03  # ESTIMATE, same planning figure as the other Spark scripts


def main() -> None:
    client = SparkClient(timeout_s=60)
    rec = {}

    def transport(body):
        raw = client(body)
        rec.update({"model": raw.get("model"), "usage": raw.get("usage"),
                    "choices": [{"message": {"content": raw["choices"][0]["message"]["content"]}}]})
        return raw

    props, usage = propose(CTX, 4, transport=transport)
    (ROOT / "tests/fixtures/spark_plan_recorded.json").write_text(json.dumps(rec, indent=2) + "\n")
    spark = {**usage, "usd": {"value": round(usage["calls"] * USD_PER_CALL, 2),
                              "label": "ESTIMATE"}}
    rep = ablation(20, proposals=props, spark=spark, p99_budget_s=CTX.p99_budget_s)
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/ablation.json").write_text(json.dumps(rep.to_dict(), indent=2) + "\n")
    print(json.dumps({"proposals": [p.params for p in props], **rep.to_dict()}, indent=1)[:2500])


if __name__ == "__main__":
    main()
