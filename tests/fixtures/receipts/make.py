"""Regenerate the receipt fixtures: python tests/fixtures/receipts/make.py

EXAMPLE numbers on a fake host. valid.json is MEASURED and billable (grade
ESTIMATE until it has a Rekor entry; built with rekor=True here so the
claimed grade is VALID). estimate.json has an ESTIMATE meter label.
noise.json has a saving inside the noise.
"""

from __future__ import annotations

import json
from pathlib import Path

from wattnot.receipt import build_receipt

HERE = Path(__file__).parent
AT = "2026-10-28T10:00:00+00:00"


def run(run_id, base, cand, label="MEASURED"):
    return {
        "run_id": run_id,
        "subject": {"model": "meta-llama/Llama-3.1-8B-Instruct",
                    "host": {"provider": "fake", "gpu": "H100 (fake)", "driver": "fake"},
                    "mode": "container",
                    "workload": {"name": "replay-v1", "version": "1", "n_requests": 200,
                                 "sha256": "0" * 64}},
        "baseline": {"params": {"power_cap_w": 700, "max_num_seqs": 256}, "wh_samples": base,
                     "p99_ms": 1000, "quality": {"fixed_pass_rate": 0.95}},
        "candidate": {"params": {"power_cap_w": 550, "max_num_seqs": 256}, "wh_samples": cand,
                      "p99_ms": 1050, "quality": {"fixed_pass_rate": 0.95}},
        "measurement": {"meter": "nvml", "hz": 10, "label": label, "idle_w": None,
                        "csv": [{"path": "traces/example.csv", "sha256": "1" * 64}]},
        "planner_trace": [{"trial_no": 1, "source": "spark", "model": "muse-spark-1.3",
                           "params": {"power_cap_w": 550},
                           "reason": "p99 1050ms is under the 1100ms budget"}],
    }


BASE = [0.0480, 0.0478, 0.0483, 0.0481, 0.0479]
CAND = [0.0432, 0.0430, 0.0435, 0.0431, 0.0433]


def main():
    out = {
        "valid": build_receipt(run("run_20261028_100000_a00001", BASE, CAND), rekor=True,
                               created_at=AT),
        "estimate": build_receipt(run("run_20261028_100000_a00002", BASE, CAND, "ESTIMATE"),
                                  created_at=AT),
        "noise": build_receipt(run("run_20261028_100000_a00003", BASE,
                                   [0.0479, 0.0482, 0.0478, 0.0481, 0.0480]), created_at=AT),
    }
    for name, r in out.items():
        (HERE / f"{name}.json").write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
