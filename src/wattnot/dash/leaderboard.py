"""Public leaderboard: net joules per correct answer, one row per setup.

Rows come from recorded step files (MEASURED) or are marked pending. A
submission is one JSON object in the shape of SUBMISSION_EXAMPLE; see
docs/joules-per-correct-answer.md.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import statistics
from pathlib import Path

SPEC = "wattnot.leaderboard/0.1"
METRIC = "net joules per correct answer (J/correct)"

SUBMISSION_EXAMPLE = {
    "spec": "wattnot.jpc-submission/0.1",
    "setup": "my-setup-name",
    "model": "org/model-name",
    "hardware": "1x H100 80GB SXM",
    "workload": "wattnot proof-v1 (math 50, code 29, qa 32)",
    "n_checked": 666, "repeats": 3, "sample_hz": 10,
    "j_per_correct": {"mean": 12.42, "sd": 0.1},
    "overhead_j_per_correct": 0.0,
    "pass_pct": 93.7, "p99_change_pct": 7.7,
    "escalation_rate_pct": None, "small_model_pass_pct": None,
    "label": "MEASURED",
    "receipt": "path or URL of a signed receipt that `wattnot verify` accepts",
}


def _sd(xs):
    return round(statistics.stdev(xs), 3) if len(xs) > 1 else None


def _row_from_step(d: dict, *, arm: str, setup: str, note: str = "") -> dict:
    js = d["j_per_req_stock" if arm == "stock" else "j_per_req_cap"]
    q = d["quality"]["stock_pct" if arm == "stock" else "cap_pct"] / 100
    per = [j / q for j in js]
    p99 = d["p99_change_pct"]
    return {"setup": setup, "model": "Llama 3.1 8B Instruct", "hardware": "1x H100 (E2E Networks)",
            "j_per_correct": {"mean": round(statistics.mean(per), 2), "sd": _sd(per)},
            "n_checked": d["quality"]["checked"], "repeats": len(js),
            "pass_pct": round(q * 100, 2),
            "p99_change_pct": 0.0 if arm == "stock" else round(p99["mean"] if isinstance(p99, dict) else p99, 2),
            "escalation_rate_pct": None, "small_model_pass_pct": None,
            "gate": "baseline" if arm == "stock" else ("passed" if d["accepted"] else "failed: " + "; ".join(d.get("why_not") or [])),
            "era": "RUN2", "label": d.get("label", "MEASURED"), "status": "measured", "note": note}


def _pending(setup, model, hardware, label, note):
    return {"setup": setup, "model": model, "hardware": hardware, "j_per_correct": None,
            "n_checked": None, "repeats": None, "pass_pct": None, "p99_change_pct": None,
            "escalation_rate_pct": None, "small_model_pass_pct": None, "gate": None,
            "era": "meta-judge", "label": label, "status": "pending", "note": note}


def rows(root: Path) -> list[dict]:
    steps = root / "data/h100-run2/vm/steps"
    out = []
    s350 = json.loads((steps / "cap350.json").read_text())
    out.append(_row_from_step(s350, arm="stock", setup="Default vLLM (no cap)",
                              note="public baseline: stock vLLM, stock 700 W limit"))
    for cap in (500, 450, 400, 350, 325):
        f = steps / f"cap{cap}.json"
        if f.exists():
            out.append(_row_from_step(json.loads(f.read_text()), arm="cap", setup=f"WattNot power cap {cap} W"))
    out += [
        _pending("WattNot small model first (Llama 3.1 8B, escalate to Muse Spark Open)", "Llama 3.1 8B + Muse Spark Open",
                 "1x H100", "MEASURED", "long GPU run: escalation rate, small-model pass rate, net of judge and agent energy"),
        _pending("WattNot cap + token trimmer, same workload", "Llama 3.1 8B Instruct", "1x H100", "MEASURED",
                 "long GPU run; today only the stacked ESTIMATE from two separate workloads exists"),
        _pending("Muse Spark Open, fewer experts", "Muse Spark Open", "1x H100", "MEASURED", "long GPU run"),
        _pending("Baseten hosted Muse Spark Open", "Muse Spark Open", "hosted (Baseten)", "ESTIMATE",
                 "provider does not expose power; joules come from tokens x a published J-per-token figure"),
        _pending("Modal hosted Muse Spark Open", "Muse Spark Open", "hosted (Modal)", "ESTIMATE",
                 "endpoint not configured (see the fallback ledger); same token-based estimate"),
    ]
    best = min((r for r in out if r["status"] == "measured" and r["gate"] in ("passed",)),
               key=lambda r: r["j_per_correct"]["mean"], default=None)
    base = out[0]["j_per_correct"]["mean"]
    for r in out:
        if r["j_per_correct"]:
            r["vs_default_pct"] = round(100 * (r["j_per_correct"]["mean"] / base - 1), 2)
        r["best"] = r is best
    return out


def meta_models(ledger_path: Path | None = None, snapshot: Path | None = None) -> list[dict]:
    """Model, job, recorded spend. From the spend ledger when present, else the committed snapshot."""
    jobs = {"muse-spark-1.3": "planner: picks the next power cap; explains gate failures; agent-waste and coding-set runs",
            "sam-3.1": "segments rack photos into units (kept off the main path)"}
    p = ledger_path or Path(os.environ.get("WATTNOT_META_LEDGER") or Path.home() / ".wattnot/meta_spend.json")
    rows = []
    if p.exists():
        d = json.loads(p.read_text())
        c = collections.defaultdict(lambda: [0, 0.0])
        for e in d.get("entries", []):
            c[e["model"]][0] += 1
            c[e["model"]][1] += float(e.get("usd") or 0)
        rows = [{"model": m, "job": jobs.get(m, ""), "calls": n, "usd": round(u, 2), "label": "MEASURED",
                 "source": "Meta API spend ledger (billed token counts x list price)"} for m, (n, u) in sorted(c.items())]
    elif snapshot and snapshot.exists():
        rows = json.loads(snapshot.read_text())["api"]
    local = [
        {"model": "Llama 3.1 8B Instruct", "job": "the served model in RUN2; small model in small-first; quality judge",
         "calls": None, "usd": None, "label": "MEASURED", "source": "own H100, cost is GPU hours (not an API bill)"},
        {"model": "Muse Spark Open (Baseten)", "job": "big model when the small one fails a check; hosted planner",
         "calls": 1, "usd": 0.004, "label": "MEASURED", "source": "docs/runs/spark-open-planner-smoke.ledger.json"},
    ]
    return rows + local


def trace_id(root: Path) -> str | None:
    f = root / "data/h100-run2/events/sweep.events.jsonl"
    return "spark-" + hashlib.sha256(f.read_bytes()).hexdigest()[:16] if f.exists() else None


def per_model_joules(root: Path) -> list[dict]:
    s = json.loads((root / "data/h100-run2/vm/steps/cap350.json").read_text())
    q = s["quality"]["checked"]
    return [{"model": "Llama 3.1 8B Instruct (served)", "j_per_answer": round(statistics.mean(s["j_per_req_cap"]), 2),
             "j_total": round(statistics.mean(s["j_per_req_cap"]) * q, 1), "label": "MEASURED",
             "how": "NVML energy counter, 10 samples a second; j_total = J per request x 666 checked answers, 350 W cap"},
            {"model": "muse-spark-1.3 (planner, 4 calls)", "j_per_answer": None, "j_total": None, "status": "not metered",
             "how": "hosted; the provider reports tokens, not joules"}]


def build(root: Path) -> dict:
    snap = root / "data/leaderboard/meta-models.json"
    return {"spec": SPEC, "metric": METRIC, "doc": "docs/joules-per-correct-answer.md",
            "rows": rows(root), "meta_models": meta_models(snapshot=snap),
            "submission_example": SUBMISSION_EXAMPLE,
            "reproduce": "scripts/reproduce_leaderboard.sh"}


if __name__ == "__main__":
    import sys
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    d = root / "data/leaderboard"
    d.mkdir(parents=True, exist_ok=True)
    api = [r for r in meta_models() if r["source"].startswith("Meta API")]
    if api:
        (d / "meta-models.json").write_text(json.dumps({"api": api}, indent=1) + "\n")
    (d / "leaderboard.json").write_text(json.dumps(build(root), indent=1) + "\n")
    (d / "submission.example.json").write_text(json.dumps(SUBMISSION_EXAMPLE, indent=1) + "\n")
    print(d / "leaderboard.json")
