"""Build the bundled SYNTHETIC fixture for `wattnot cloud demo`.

No API call is made. Replies are made up by a seeded generator with plausible
shapes: harder prompt classes reason longer, low effort gets some hard items
wrong, one low-effort answer runs out of tokens while thinking. Every receipt
made from this file is labelled SIMULATED. Replace it with a recorded run by
passing --record to a live `wattnot cloud tune`.

    python scripts/gen_cloud_fixture.py            # rewrites data/cloud/demo-*.json
"""

from __future__ import annotations

import itertools
import json
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wattnot.cloud import MODEL  # noqa: E402
from wattnot.cloud.cache import ARMS, QUESTIONS, load_doc  # noqa: E402
from wattnot.cloud.efforts import DEFAULT_ALLOWANCE  # noqa: E402
from wattnot.cloud.run import WORKLOAD, Options, run  # noqa: E402
from wattnot.cloud.transport import FIXTURE_FORMAT, FixtureTransport  # noqa: E402
from wattnot.runner.workload import load_workload  # noqa: E402

BASE_REASONING = {"extract": 900, "classify": 700, "trace": 1400, "arith": 2600, "logic": 6500}
SCALE = {"low": 0.12, "medium": 0.30, "high": 0.62, "xhigh": 1.0}
WRONG = {"arith-3": "25", "logic-1": "Cara", "logic-2": "2.5", "logic-3": "40"}
FAILS = {("arith-3", "low"), ("logic-1", "low"), ("logic-2", "low"), ("logic-3", "low"),
         ("logic-2", "medium")}
TRUNCATED = {("logic-3", "low")}
ORDERS = {"extract": ["low", "medium", "high"], "classify": ["low", "medium", "high"],
          "trace": ["low", "medium", "high"], "arith": ["medium", "low", "high"],
          "logic": ["high", "medium", "low"]}


_IDS = itertools.count(1)


def reply(status, content, prompt, completion, reasoning, latency, *, cached=0,
          finish="stop", rlen=None):
    msg = {"role": "assistant", "content": content}
    if reasoning:
        msg["reasoning_chars"] = reasoning * 4
        msg["reasoning_sha256"] = "0" * 64
    return {"status": status, "latency_s": round(latency, 3), "retries": 0,
            "headers": {"x-ratelimit-remaining-requests": "110",
                        "x-ratelimit-remaining-tokens": "480000"},
            "body": {"id": f"chatcmpl-sim-{next(_IDS):06d}", "model": MODEL,
                     "choices": [{"message": msg, "finish_reason": finish}],
                     "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                               "total_tokens": prompt + completion,
                               "prompt_tokens_details": {"cached_tokens": cached},
                               "completion_tokens_details": {"reasoning_tokens": reasoning}}}}


def build() -> dict:
    rng = random.Random(1026)
    replies = {}
    items = list(load_workload(WORKLOAD).items)
    for it in items:
        cls, gold = it.meta["class"], it.meta["gold"]
        for effort, k in SCALE.items():
            for rep in range(3):
                r = int(BASE_REASONING[cls] * k * rng.uniform(0.88, 1.12))
                prompt = 60 + len(it.prompt) // 4
                if (it.id, effort) in TRUNCATED:
                    comp = DEFAULT_ALLOWANCE[effort] + it.max_tokens
                    replies[f"{it.id}|{effort}|{rep}"] = reply(
                        200, "", prompt, comp, comp, comp / 95 + 1.1, finish="length")
                    continue
                answer = WRONG[it.id] if (it.id, effort) in FAILS else gold
                comp = r + 14
                replies[f"{it.id}|{effort}|{rep}"] = reply(
                    200, f"ANSWER: {answer}", prompt, comp, r,
                    1.1 + comp / 95 * rng.uniform(0.9, 1.1))
    names = sorted(ORDERS)
    note = "start low where the answer is a lookup, start at high where it is multi-step"
    plan = json.dumps({"orders": ORDERS, "note": note})
    replies["propose"] = reply(200, plan, 140 + 40 * len(names), 90, 60, 2.4)
    # cache A/B: 8 calls per arm over a ~2.3k token document
    doc_tokens = len(load_doc()) // 4
    for arm in ARMS:
        for i in range(8):
            q = len(QUESTIONS[i % len(QUESTIONS)]) // 4
            prompt = doc_tokens + q + 12
            if arm == "volatile-first" or i == 0:
                cached = 0
            elif arm == "stable-first":
                cached = int(doc_tokens * 0.99) if i % 3 else 0  # some land on a cold replica
            else:
                cached = int(doc_tokens * 0.99)
            lat = 1.6 - (0.5 if cached else 0) + rng.uniform(-0.1, 0.1)
            replies[f"cache|{arm}|{i}"] = reply(200, "Section 3.", prompt, 20, 12, lat,
                                                cached=cached)
    # half-life probe
    prompt = doc_tokens + len(QUESTIONS[0]) // 4 + 12
    for w, share in ((10, .99), (30, .99), (60, .97), (180, .45), (600, 0.0)):
        replies[f"probe|warm|{w}"] = reply(200, "Section 3.", prompt, 8, 4, 1.7)
        replies[f"probe|read|{w}"] = reply(200, "Section 3.", prompt, 8, 4, 1.2,
                                           cached=int(doc_tokens * share))
    return {"format": FIXTURE_FORMAT, "source": "synthetic", "model": MODEL,
            "note": "made up by scripts/gen_cloud_fixture.py; no API call was made",
            "replies": replies}


def meter_for(rows: list[dict]) -> dict:
    """What the usage endpoint would return for these calls, with a small drift."""

    ok = [r for r in rows if r["status"] == 200]
    prompt = sum(r["prompt_tokens"] for r in ok)
    cached = sum(r["cached_tokens"] for r in ok)
    out = sum(r["completion_tokens"] for r in ok)
    return {"note": "SIMULATED usage response for the demo receipt (0.4% drift on output)",
            "items": [{"start_time": "2026-10-27T06:00:00Z", "end_time": "2026-10-27T06:01:00Z",
                       "results": [{"model": MODEL, "request_count": len(ok),
                                    "input_tokens": prompt, "cached_input_tokens": cached,
                                    "uncached_input_tokens": prompt - cached,
                                    "output_tokens": round(out * 1.004)}]}],
            "pagination": {"has_more": False, "cursor": None}}


def main() -> int:
    fx = build()
    out = ROOT / "data" / "cloud"
    out.mkdir(parents=True, exist_ok=True)
    (out / "demo-fixture.json").write_text(json.dumps(fx, indent=1, sort_keys=True) + "\n")
    with tempfile.TemporaryDirectory() as d:
        path, _ = run(Options(), FixtureTransport(fx), "replay-synthetic", out_dir=d)
        rows = [json.loads(x) for x in
                path.with_name(path.stem + ".calls.jsonl").read_text().splitlines()]
    (out / "demo-meter.json").write_text(json.dumps(meter_for(rows), indent=1) + "\n")
    print(f"wrote {out}/demo-fixture.json ({len(fx['replies'])} replies) and demo-meter.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
