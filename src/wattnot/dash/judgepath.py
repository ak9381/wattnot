"""The judge path: one JSON bundle for the "Proof" tab and the static replay.

Every block carries its own label. RUN2 numbers (Granite quality judge) stay
in their own block; Meta-judge numbers go in `meta_judge` once measured.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from wattnot.planner import trace as _trace
from wattnot.route.stack import stack_row
from wattnot.verify import verify_chain, verify_document

SPEC = "wattnot.judge-path/1"


def _load(p: Path):
    return json.loads(p.read_text()) if p.exists() else None


def run2_block(root: Path) -> dict | None:
    d = _load(root / "data/h100-run2/vm/steps/cap350.json")
    if not d:
        return None
    js, jc = statistics.mean(d["j_per_req_stock"]), statistics.mean(d["j_per_req_cap"])
    qs, qc = d["quality"]["stock_pct"] / 100, d["quality"]["cap_pct"] / 100
    a, b = js / qs, jc / qc
    return {"era": "RUN2", "judge": "fixed answer checks (Granite era)", "label": "MEASURED",
            "cap_w": 350, "checked": d["quality"]["checked"],
            "j_per_correct": {"stock": round(a, 2), "cap": round(b, 2),
                              "change_pct": round(100 * (b / a - 1), 2)},
            "pass_pct": {"stock": d["quality"]["stock_pct"], "cap": d["quality"]["cap_pct"]}}


def stack_block(root: Path, run2: dict | None) -> dict | None:
    v3 = _load(root / "data/agent-waste/v3/results.json")
    if not v3 or not run2:
        return None
    inj, nor = v3["injected"], v3["normal"]
    row = stack_row(tokens_before=inj["harness"]["total_tokens"],
                    tokens_after=inj["harness+saver"]["total_tokens"], tokens_label="MEASURED",
                    j_per_token_change_pct=run2["j_per_correct"]["change_pct"], caps_label="MEASURED",
                    trimmer="WattNot stop rule (agent-waste v3)", same_workload=False)
    row["normal_tasks"] = {"tokens_change_pct": nor.get("saver_vs_harness"), "label": "MEASURED",
                           "note": "on tasks where the harness already stops well, the saver adds a little"}
    return row


def verify_block(root: Path) -> dict:
    d = root / "verifier/samples/chain"
    files = sorted(p for p in d.glob("*.json") if not p.name.endswith(".sigstore.json"))
    recs = [json.loads(p.read_text()) for p in files]
    out = []
    for p, r in zip(files, recs):
        b = p.with_name(p.stem + ".sigstore.json")
        rep = verify_document(r, _load(b), path=p.name)
        out.append({"file": p.name, "ok": all(s.ok for s in rep.steps), "grade": rep.grade,
                    "steps": [{"name": s.name, "ok": s.ok} for s in rep.steps]})
    ch = verify_chain(recs)
    return {"label": "MEASURED", "receipts": out, "chain_ok": ch.ok,
            "command": "wattnot verify --chain verifier/samples/chain/"}


def build(root: Path) -> dict:
    run2 = run2_block(root)
    ev = root / "data/h100-run2/events/sweep.events.jsonl"
    health = sorted((root / "data/judge").glob("health-*.json"))
    return {
        "spec": SPEC,
        "run2": run2,
        "meta_judge": {"label": "PENDING", "judge": "Llama 3.1 8B Instruct",
                       "note": "measured in the long GPU run; kept apart from RUN2"},
        "trace": _trace.from_file(ev) if ev.exists() else None,
        "explain": _load(root / "data/explain/run2.json"),
        "health": _load(health[-1]) if health else None,
        "stack": stack_block(root, run2),
        "escalation": {"label": "PENDING", "small": "Llama-3.1-8B-Instruct",
                       "big": "Muse-Spark-Open-816B-A42B",
                       "note": "per-item small and big answers are measured in the long GPU run; "
                               "the card reports escalation rate, the small model's own pass rate "
                               "and net savings after judge and agent energy",
                       "preview": {"label": "MOCK", "escalation_rate_pct": 35.0,
                                   "small_model_pass_pct": 65.0, "net_saved_pct": 41.0}},
        "verify": verify_block(root),
    }


if __name__ == "__main__":
    import sys
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    out = root / "data/judge/judge-path.json"
    out.write_text(json.dumps(build(root), indent=1) + "\n")
    print(out)
