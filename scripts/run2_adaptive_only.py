#!/usr/bin/env python3
# HISTORICAL (RUN2, Oct 27-30): kept as the record of the earlier test. It used non-Meta
# models (Granite / Qwen) and is not called by the long run. Every model WattNot runs now is a Meta model.
"""Run only the adaptive top-k step on a GPU host that already has the kit and weights.

    python scripts/run2_adaptive_only.py HOST [--out DIR] [--budget-s 900]

Used in RUN2 after the control machine's SSH session to the host hung mid-sweep: the sweep
numbers were already on the host, so the adaptive step was run on its own. Events go to
DIR/live/adaptive.events.jsonl in the same schema as the full orchestrated run.
"""
import argparse
import json
from pathlib import Path

from wattnot.proof.orchestrate import Live, new_run_id, vm_line
from wattnot.proof.transport import SshTransport

ap = argparse.ArgumentParser()
ap.add_argument("host")
ap.add_argument("--out", type=Path, default=Path("run2-adaptive"))
ap.add_argument("--key", default="~/.ssh/e2e_h100")
ap.add_argument("--budget-s", type=float, default=900)
ap.add_argument("--model", default="meta-models/Muse-Glimmer-30B")
ap.add_argument("--adaptive-model", default="ibm-granite/granite-3.1-3b-a800m-instruct")
a = ap.parse_args()
(a.out / "live").mkdir(parents=True, exist_ok=True)
live = Live(new_run_id(), a.out, a.out / "live" / "adaptive.events.jsonl", mock=False)
grabbed: dict = {}
common = ["--out", "/root/run2/results", "--port", "8000", "--model", a.model, "--repeat", "2",
          "--concurrency", "32", "--settle-s", "4.0", "--warmup", "16", "--emit"]
rc = SshTransport(a.host, key=a.key).run(
    ["adaptive", *common, "--adaptive-model", a.adaptive_model,
     "--adaptive-budget-s", str(a.budget_s)],
    vm_line(live, grabbed), timeout_s=a.budget_s + 1800)
print("exit", rc)
print(json.dumps(grabbed.get("ADAPTIVE_JSON"), indent=1))
