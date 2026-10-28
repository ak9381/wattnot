"""Build the RUN2 350 W receipt from the recorded step file and sign it offline.

The signature uses the public test key (verifier/samples/test-key.pem), so the
grade is capped at ESTIMATE and it proves only that the file was not edited
after signing. A public Rekor entry needs the owner's go-ahead.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wattnot.dash import leaderboard as lb  # noqa: E402
from wattnot.planner import trace as tr  # noqa: E402
from wattnot.receipt.build import build_receipt  # noqa: E402
from wattnot.receipt.sign import sign  # noqa: E402

STEP = ROOT / "data/h100-run2/vm/steps/cap350.json"
EVENTS = ROOT / "data/h100-run2/events/sweep.events.jsonl"
OUT = ROOT / "receipts/run2"


def main() -> Path:
    s = json.loads(STEP.read_text())
    q = s["quality"]
    t = tr.from_file(EVENTS)
    run = {
        "run_id": "run_20261027_run2_cap350", "receipt_id": "rcpt_20261027_run2_cap350",
        "subject": {"model": "meta-llama/Llama-3.1-8B-Instruct",
                    "host": {"provider": "e2e-networks", "gpu": "H100 80GB", "driver": "recorded"},
                    "mode": "container",
                    "workload": {"name": "proof-v1", "version": "1", "n_requests": q["checked"],
                                 "sha256": hashlib.sha256(STEP.read_bytes()).hexdigest()}},
        "baseline": {"params": {"power_cap_w": 700}, "wh_samples": [j / 3600 for j in s["j_per_req_stock"]],
                     "p99_ms": round(s["p99_s"]["stock"] * 1000), "quality": {"fixed_pass_rate": q["stock_pct"] / 100}},
        "candidate": {"params": {"power_cap_w": 350}, "wh_samples": [j / 3600 for j in s["j_per_req_cap"]],
                      "p99_ms": round(s["p99_s"]["cap"] * 1000), "quality": {"fixed_pass_rate": q["cap_pct"] / 100}},
        "measurement": {"meter": "nvml", "hz": 10, "label": "MEASURED", "idle_w": None,
                        "csv": [{"path": "data/h100-run2/vm/steps/cap350.json",
                                 "sha256": hashlib.sha256(STEP.read_bytes()).hexdigest()}],
                        "spark_trace_id": lb.trace_id(ROOT),
                        "per_model_joules": lb.per_model_joules(ROOT)},
        "planner_trace": [{"trial_no": c["n"], "source": "fallback" if c["fallback"] else "spark",
                           "model": c["model"], "params": {"power_cap_w": c.get("cap_w")},
                           "reason": c.get("reasoning") or ""} for c in t["calls"]],
    }
    r = build_receipt(run, created_at="2026-10-27T15:00:00+00:00")
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{r['receipt_id']}.json"
    p.write_text(json.dumps(r, indent=2) + "\n")
    sign(p, no_rekor=True, key_path=ROOT / "verifier/samples/test-key.pem")
    return p


if __name__ == "__main__":
    print(main())
