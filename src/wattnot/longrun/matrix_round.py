"""One round of the 12-round matrix, dry: our set + GSM8K + HumanEval, without and with WattNot,
every answer graded deterministically AND by Muse Spark (MOCK here), written in the shape the
dashboard's Run matrix page reads (out/matrix/<round>.json).

    python -m wattnot.longrun.matrix_round --dry-run --round r01 --out /tmp/matrix-dry
    python -m wattnot.longrun.matrix_round --estimate          # Meta $ for all 12 rounds, no calls

--dry-run only: canned answers (an oracle that slips on a few items in the WattNot arm), FakeNvml,
Spark MOCK. Live rounds run through the driver/harness with WATTNOT_MATRIX_ROUND set; this file
never starts a GPU or a paid call. The default --out is outside the repo so a dry run never shows
up on the dashboard as a real round.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from .spark_grade import CONFIG, SparkGrader, load_config, request_body

ROOT = Path(__file__).resolve().parents[3]


def _bench():
    spec = importlib.util.spec_from_file_location("public_bench_ab", ROOT / "scripts" / "public_bench_ab.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _slip(i: int, arm: str) -> bool:
    return arm == "with_wattnot" and i % 23 == 7   # canned: a few misses in the capped arm


def _our_set(spark: SparkGrader, arm: str) -> tuple[int, int]:
    from wattnot.proof.fake import FakeNvml
    from .grade import grade_one
    from .harness import FakeChat
    from .tasks import build
    tasks = build()
    nv = FakeNvml()
    h = nv.device_handle(0)
    nv.set_power_limit_mw(h, (700 if arm == "without_wattnot" else 325) * 1000)
    chat = FakeChat(nv, h, tasks)
    ok = sum(grade_one(t, chat, spark).ok for t in tasks)
    return ok, len(tasks)


def _public(spark: SparkGrader, arm: str, b) -> dict[str, tuple[int, int]]:
    out = {}
    for name in ("gsm8k", "humaneval"):
        items = b.load(name)
        pf, gf = b.grader(name)
        ok = 0
        for i, x in enumerate(items):
            if name == "gsm8k":
                gold = b.gsm8k_gold(x)
                text = f"#### {int(gold) + (1 if _slip(i, arm) else 0)}" if gold is not None else ""
            else:
                body = x["canonical_solution"] if not _slip(i, arm) else "    return None\n"
                text = "```python\n" + x["prompt"] + body + "```"
            k = bool(gf(x, text))
            ok += k
            spark(x.get("task_id", i), name, pf(x), text, k)
        out[name] = (ok, len(items))
    return out


def dry_round(round_id: str, out_dir: Path, config: Path | None = None) -> dict:
    cfg = load_config(config)
    r = next((x for x in cfg.get("rounds", []) if x["id"] == round_id), None)
    if r is None:
        raise SystemExit(f"unknown round {round_id!r}")
    b = _bench()
    spark = SparkGrader(mock=True, round_id=round_id, model=cfg.get("spark_grade_model"))
    acc = {}
    for arm in r["arms"]:
        spark.arm = arm
        ours = _our_set(spark, arm)
        pub = _public(spark, arm, b)
        tot_ok = ours[0] + sum(v[0] for v in pub.values())
        tot_n = ours[1] + sum(v[1] for v in pub.values())
        acc[arm] = {"ours": ours, **pub, "accuracy": round(tot_ok / tot_n, 4)}
    doc = {"round": round_id, "leg": r["leg"], "status": "dry-run", "label": "MOCK",
           "note": "dry run: canned answers, FakeNvml, Spark MOCK; no GPU, no paid call",
           "spark_grade_all": bool(cfg.get("spark_grade_all")), "deterministic_decides": True,
           "results": {"accuracy_without": acc["without_wattnot"]["accuracy"],
                       "accuracy_with": acc["with_wattnot"]["accuracy"], "per_arm": acc},
           "spark_summary": spark.summary(), "answers": spark.records}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{round_id}.json").write_text(json.dumps(doc, indent=1))
    return doc


def estimate(config: Path | None = None) -> dict:
    """Worst-case Meta $ for grading every answer in all rounds (guard pricing), no calls."""
    from wattnot.meta.ledger import estimate as worst
    cfg = load_config(config)
    per_round = sum(d["items"] for d in cfg["datasets"].values()) * 2
    n = per_round * len(cfg["rounds"])
    body = request_body(cfg.get("spark_grade_model", "muse-spark-1.3"), "x" * 600, "x" * 900)
    one = worst(body)[2]
    return {"answers": n, "per_round": per_round, "worst_case_usd_per_grade": one,
            "worst_case_usd_total": round(one * n, 2) if one else None, "label": "ESTIMATE",
            "cap_usd": cfg["meta_budget"]["cap_usd"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--estimate", action="store_true")
    ap.add_argument("--round", default="all", help="r01..r12 or all")
    ap.add_argument("--out", type=Path, default=Path("/tmp/matrix-dry"))
    ap.add_argument("--config", type=Path, default=CONFIG)
    a = ap.parse_args(argv)
    if a.estimate:
        print(json.dumps(estimate(a.config), indent=1))
        return 0
    ids = [r["id"] for r in load_config(a.config)["rounds"]] if a.round == "all" else [a.round]
    for rid in ids:
        d = dry_round(rid, a.out, a.config)
        s = d["spark_summary"]
        print(f"[matrix] {rid} {d['leg']:<9} acc {d['results']['accuracy_without']:.3f} -> "
              f"{d['results']['accuracy_with']:.3f}  spark {s['spark_graded']}/{s['answers']} "
              f"agree {s['agreement']} [{d['label']}]", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
