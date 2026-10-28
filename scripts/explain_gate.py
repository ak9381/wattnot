"""Explain RUN2's gate failures with one batched Muse Spark call (rules if it fails).

    python scripts/explain_gate.py [--offline] [-o data/explain/run2.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from wattnot.gates.explain import explain, failures_from_events, spark_transport

EVENTS = Path("data/h100-run2/events/sweep.events.jsonl")
CLASSES = {"math": 50, "code": 29, "qa": 32}  # proof-v1, 111 prompts


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--offline", action="store_true")
    p.add_argument("-o", "--out", type=Path, default=Path("data/explain/run2.json"))
    a = p.parse_args()
    ev = [json.loads(x) for x in EVENTS.read_text().splitlines() if x.strip()]
    fails = failures_from_events(ev, CLASSES)
    tr = None if a.offline else spark_transport()
    out = explain(fails, transport=tr)
    out["run"] = "RUN2"
    out["source_events"] = str(EVENTS)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=2) + "\n")
    for i in out["items"]:
        print(f"[{i['source']}] {i['setting']} {i['check']}: {i['prompt_class']} - {i['reason']}")
    print("fallback:", out.get("fallback"), "tokens:", out.get("tokens"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
