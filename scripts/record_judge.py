"""Record one live Muse Spark judge call on the public 30-item set.

    META_API_KEY=... python scripts/record_judge.py

One call. Writes tests/fixtures/judge_recorded.json (response with usage)
and prints the agreement with the human labels.
"""

from __future__ import annotations

import json
from pathlib import Path

from wattnot.gates.judge import agreement, load_judge_set, parse_verdicts, request_body
from wattnot.planner.spark import SparkClient

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "judge_recorded.json"


def main() -> None:
    items = load_judge_set()
    raw = SparkClient(timeout_s=120)(request_body(items))
    keep = {"model": raw.get("model"), "choices": [{"message": {
        "content": raw["choices"][0]["message"]["content"]}}], "usage": raw.get("usage")}
    OUT.write_text(json.dumps(keep, indent=2) + "\n", encoding="utf-8")
    v = parse_verdicts(keep, [i["id"] for i in items])
    print(json.dumps({"usage": raw.get("usage"), **agreement(items, v)}))


if __name__ == "__main__":
    main()
