#!/usr/bin/env python3
"""Run the Code Mode *sandbox* over the repo's recorded candidates and write a signed receipt.

Where ``scripts/codemode_detect.py`` only *models* the Code Mode "after", this script actually
runs each candidate's task as one generated program in a locked-down subprocess (no network,
CPU/memory/wall limits, no writes outside a temp dir, minimal env) and measures what the run
cost. It writes:

* ``data/agent-waste/codemode/sandbox_results.json`` -- the dashboard card reads this next to
  the detection results; every field keeps its MEASURED / TOKENIZER / ESTIMATE label.
* ``receipts/codemode/codemode-sandbox.json``        -- a before/after receipt, signed offline
  with the local key if one is configured (otherwise clearly marked unsigned; signing never
  changes the hash or lifts a grade).

No network, no paid calls, no GPU, no servers. Regenerate with:

    python scripts/codemode_sandbox.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from wattnot.agentwaste.sandbox.run import build, build_receipt
from wattnot.agentwaste.sandbox.runner import Limits
from wattnot.receipt.sign import sign_run_receipt

ROOT = Path(__file__).resolve().parents[1]
TRACES = ROOT / "data" / "agent-waste" / "v3" / "runs.jsonl"
RESULTS = ROOT / "data" / "agent-waste" / "codemode" / "sandbox_results.json"
RECEIPT = ROOT / "receipts" / "codemode" / "codemode-sandbox.json"
SOURCE = ("recorded Muse Spark agent runs (data/agent-waste/v3/runs.jsonl), replayed as a "
          "typed tool API and run as one program in a locked-down sandbox")


def load_traces(path: Path = TRACES) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> None:
    runs = load_traces()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = build(runs, limits=Limits(), now=now)

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    receipt = build_receipt(results, source=SOURCE, now=now)
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    bundle, signer = sign_run_receipt(RECEIPT)

    print(f"ran {results['candidates']} Code Mode candidate(s) in the sandbox "
          f"(net isolation: {results['env']['net_isolation']}, "
          f"RAPL readable: {results['env']['rapl_readable']})")
    for row in results["rows"]:
        sb, af, sv = row["sandbox"], row["after"], row["saved"]
        print(f"  {row['task']}: ok={sb['ok']} tool_calls={sb['tool_calls']} "
              f"wall={af['sandbox_wall_s']['value']}s cpu={af['cpu_s']['value']}s "
              f"code_tokens={af['code_tokens']['value']} "
              f"saved round_trips={sv['round_trips']['value']} tokens={sv['total_tokens']['value']}")
    print(f"results -> {RESULTS.relative_to(ROOT)}")
    print(f"receipt -> {RECEIPT.relative_to(ROOT)} (signer: {signer})")
    if bundle:
        print(f"bundle  -> {bundle.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
