#!/usr/bin/env python3
"""Run Code Mode detection over the repo's recorded agent traces and write a signed receipt.

Input is the existing recorded traces under ``data/agent-waste/v3/runs.jsonl`` (real Muse
Spark runs, re-read here -- nothing runs on a GPU or over the network). We detect the runs
that look like Code Mode candidates (a big tool list taken over many round trips), model the
Code Mode "after" (ESTIMATE), and write:

* ``data/agent-waste/codemode/results.json`` -- the dashboard card reads this.
* ``receipts/codemode/codemode-demo.json``   -- the before/after receipt, signed offline with
  the local key if one is configured (otherwise clearly marked unsigned; signing never
  changes the hash or lifts a grade).

No network, no paid calls, no servers. Regenerate with:  python scripts/codemode_detect.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from wattnot.agentwaste.codemode import CodeModeConfig, analyze, build_receipt
from wattnot.receipt.sign import sign_run_receipt

ROOT = Path(__file__).resolve().parents[1]
TRACES = ROOT / "data" / "agent-waste" / "v3" / "runs.jsonl"
RESULTS = ROOT / "data" / "agent-waste" / "codemode" / "results.json"
RECEIPT = ROOT / "receipts" / "codemode" / "codemode-demo.json"
SOURCE = "recorded Muse Spark agent runs (data/agent-waste/v3/runs.jsonl), re-read offline"


def load_traces(path: Path = TRACES) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> None:
    cfg = CodeModeConfig()
    runs = load_traces()
    results = analyze(runs, cfg)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results["generated"] = now
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    receipt = build_receipt(results, source=SOURCE, now=now)
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    bundle, signer = sign_run_receipt(RECEIPT)

    print(f"examined {results['runs_examined']} runs, {results['candidates']} Code Mode candidates")
    tot = results["totals"]
    print(f"round trips {tot['round_trips']['before']['value']} -> "
          f"{tot['round_trips']['after']['value']} (saved {tot['round_trips']['saved']['value']})")
    print(f"schema tokens resaved {tot['schema_tokens_resent']['saved']['value']}, "
          f"total tokens saved {tot['total_tokens']['saved']['value']}, "
          f"$ saved {tot['usd']['saved']['value']}")
    print(f"results -> {RESULTS.relative_to(ROOT)}")
    print(f"receipt -> {RECEIPT.relative_to(ROOT)} (signer: {signer})")
    if bundle:
        print(f"bundle  -> {bundle.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
