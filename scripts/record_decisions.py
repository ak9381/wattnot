"""Record a few Muse Spark planner answers for the offline tests.

    META_API_KEY=... python scripts/record_decisions.py

One call per scenario, each with a made-up telemetry summary (numbers only).
Saves the request summary and the raw API response to
tests/fixtures/spark/decisions.json. Tests replay this file.

Recorded Oct 27 2026: the first four calls used reasoning effort "low" and a
400-token cap, ran out of tokens while reasoning and returned no content
(kept in truncated.json; they test the fallback). The planner now asks for
"minimal" effort with a 600-token cap; the headroom scenario was re-recorded
with that.
"""

from __future__ import annotations

import json
from pathlib import Path

from wattnot.planner import SparkClient, summarize

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "spark" / "decisions.json"
KNOBS = {"max_num_seqs": 256, "enable_prefix_caching": True, "quantization": "none"}

SCENARIOS = {
    "headroom": summarize(cap_w=300, min_w=200, max_w=700, step_w=25, base_p99_s=1.20,
                          p99_s=1.135, slack=0.10, wh_per_req=0.0412, base_wh_per_req=0.0480,
                          gates=[{"cap_w": 300, "passed": True}], knobs=KNOBS),
    "after_fail": summarize(cap_w=300, min_w=200, max_w=700, step_w=25, base_p99_s=1.20,
                            p99_s=1.29, slack=0.10, wh_per_req=0.0398, base_wh_per_req=0.0480,
                            gates=[{"cap_w": 300, "passed": True},
                                   {"cap_w": 275, "passed": False, "failed": ["p99"]}],
                            knobs=KNOBS),
    "tight": summarize(cap_w=350, min_w=200, max_w=700, step_w=25, base_p99_s=1.20,
                       p99_s=1.30, slack=0.10, wh_per_req=0.0440, base_wh_per_req=0.0480,
                       gates=[{"cap_w": 350, "passed": True}],
                       knobs={**KNOBS, "enable_prefix_caching": False}),
    "start": summarize(cap_w=700, min_w=200, max_w=700, step_w=25, base_p99_s=1.20,
                       p99_s=1.20, slack=0.10, wh_per_req=0.0480, base_wh_per_req=0.0480,
                       gates=[], knobs=KNOBS),
}


def main() -> None:
    client = SparkClient()
    out = {}
    for name, summary in SCENARIOS.items():
        out[name] = {"summary": summary, "response": client(client.request_body(summary))}
        print(name, out[name]["response"]["choices"][0]["message"]["content"],
              out[name]["response"].get("usage"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
