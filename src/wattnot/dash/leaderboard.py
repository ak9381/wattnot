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
SUBMISSION_SPEC = "wattnot.jpc-submission/0.1"
# Published joules-per-token for a hosted frontier model (Muse Spark Open on H100-class GPUs):
# RUN2 measured 0.1497 J/token on Llama 3.1 8B, scaled by active params (42B / 0.8B). ESTIMATE.
J_PER_TOKEN = 7.86

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


def _hosted_est_row(setup, host, tokens_per_answer, pass_pct, source):
    """One hosted ESTIMATE row: joules from billed tokens x J_PER_TOKEN, net of the pass rate."""
    jpa = tokens_per_answer * J_PER_TOKEN
    jpc = round(jpa / (pass_pct / 100), 2) if pass_pct else None
    return {"setup": setup, "model": "Muse Spark Open", "hardware": f"hosted ({host})",
            "j_per_correct": None if jpc is None else {"mean": jpc, "sd": None},
            "n_checked": None, "repeats": None, "pass_pct": round(pass_pct, 2),
            "p99_change_pct": None, "escalation_rate_pct": None, "small_model_pass_pct": None,
            "gate": "hosted baseline (no cap/quant/top-k possible on a shared endpoint)",
            "era": "hosted", "label": "ESTIMATE", "status": "estimate",
            "note": f"ESTIMATE: {round(tokens_per_answer)} billed tokens/answer x {J_PER_TOKEN} J/token "
                    f"(provider gives no power); token counts MEASURED from {source}"}


def hosted_rows(root: Path) -> list[dict]:
    """Baseten and Modal hosted Muse Spark Open rows, ESTIMATE from measured token counts."""
    reports = sorted((root / "reports/hosted-ab").glob("live-*.json")) if \
        (root / "reports/hosted-ab").exists() else []
    if not reports:
        return [_pending("Baseten hosted Muse Spark Open", "Muse Spark Open", "hosted (Baseten)",
                         "ESTIMATE", "provider does not expose power; tokens x a J-per-token figure"),
                _pending("Modal hosted Muse Spark Open", "Muse Spark Open", "hosted (Modal)",
                         "ESTIMATE", "same token-based estimate as Baseten")]
    d = json.loads(reports[-1].read_text())
    src = reports[-1].name
    base = d.get("hosts", {}).get("baseten", {}).get("without", {})
    tok = base.get("tokens") or {}
    calls = tok.get("calls") or base.get("calls") or d.get("hosts", {}).get("baseten", {}).get("without", {}).get("calls", 0)
    # energy tokens = uncached prompt + completion (cached prompt tokens skip prefill)
    energy_tokens = (tok.get("prompt", 0) - tok.get("cached", 0)) + tok.get("completion", 0)
    per_answer = (energy_tokens / calls) if calls else 0
    passv = (base.get("pass_pct") or {}).get("value", 100.0)
    rows_out = [_hosted_est_row("Baseten hosted Muse Spark Open", "Baseten", per_answer, passv, src)]
    # Modal shares the endpoint and list price, but we have no Modal-specific token counts yet, so
    # its row stays pending (ESTIMATE when a Modal run records its own tokens) rather than mirror
    # Baseten's numbers and look measured.
    rows_out.append(_pending("Modal hosted Muse Spark Open", "Muse Spark Open", "hosted (Modal)",
                             "ESTIMATE", "same token-based estimate as Baseten, pending a Modal run"))
    return rows_out


def validate_submission(obj: dict) -> list[str]:
    """Validate a leaderboard submission against schemas/jpc-submission.schema.json.

    Returns a list of human-readable problems ([] when the submission is valid). Pure stdlib
    jsonschema; no network, no receipt verification (that is a separate `wattnot verify` step).
    """
    from jsonschema import Draft202012Validator

    from wattnot.schemas import schema_dir
    schema = json.loads((schema_dir() / "jpc-submission.schema.json").read_text())
    v = Draft202012Validator(schema)
    return [f"{'/'.join(str(p) for p in e.path) or '(root)'}: {e.message}"
            for e in sorted(v.iter_errors(obj), key=lambda e: list(e.path))]


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
        _pending("WattNot three-tier chain (Llama 3.1 8B -> Muse Glimmer 30B -> Muse Spark Open)",
                 "Llama 3.1 8B + Muse Glimmer 30B + Muse Spark Open", "1x H100", "MEASURED",
                 "long GPU run: per-tier escalation rate and small-model pass rate, net of judge/gate energy"),
        _pending("WattNot cap + token trimmer, same workload", "Llama 3.1 8B Instruct", "1x H100", "MEASURED",
                 "long GPU run; today only the stacked ESTIMATE from two separate workloads exists"),
        _pending("Muse Spark Open, fewer experts", "Muse Spark Open", "1x H100", "MEASURED", "long GPU run"),
    ]
    out += hosted_rows(root)
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
        {"model": "Llama 3.1 8B Instruct", "job": "the served model in RUN2; small model in small-first; judge fallback",
         "calls": None, "usd": None, "label": "MEASURED", "source": "own H100, cost is GPU hours (not an API bill)"},
        {"model": "Muse Spark Open (Baseten)", "job": "big model when the small one fails a check; hosted planner; default quality judge",
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
            "submission_schema": "schemas/jpc-submission.schema.json",
            "j_per_token_estimate": J_PER_TOKEN,
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
