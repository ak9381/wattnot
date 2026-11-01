#!/usr/bin/env python3
"""Multi-provider routing: the same prompts on the H100 (E2E, our own Glimmer), Baseten (hosted
Muse Spark Open) and Modal (serverless GPU, our own Glimmer), then each request class goes to the
provider with the best energy or cost per correct answer that still passes the quality gate.

Modes:
  --plan    (default) the spend plan: calls per provider and a $ ESTIMATE. Calls nothing.
  --replay  a routing decision from numbers already recorded (labelled REPLAY). The recorded
            runs did NOT use the same prompts, so the decision is a worked example only.
  --results FILE  decide from a real same-prompt results file (written by the live block).

Baseten and Modal are paid. This script never calls them: the live commands it prints need
Akash's go, and they run inside the existing spend guards (meta ledger / hosted_ab --cap-usd,
Modal from the $100 credit).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLASSES = {"own": "own set (proof-v1 math)", "gsm8k300": "GSM8K", "humaneval164": "HumanEval"}
GATE_PTS = 1.0   # a provider may be at most 1 point below the best pass rate for that class

# Spend caps for combined leg 4 (Baseten + Modal share). The Baseten key is the PAID workspace key
# BASETEN_METRICS_KEY only (the free BASETEN_API_KEY is dead and never used). The Modal share runs
# from the $100 Modal credit.
BASETEN_ROUND_CAP_USD = 8.0      # HARD stop per Baseten round: hosted_ab --cap-usd 8
META_SOFT_CAP_USD = 100.0        # Meta (grading) spend soft cap: warn and keep going
META_HARD_CAP_USD = 120.0        # Meta (grading) spend hard cap: stop
BASETEN_KEY_ENV = "BASETEN_METRICS_KEY"


class CapExceeded(RuntimeError):
    """A recorded provider spend went past its hard cap: the round is a hard stop, not a result."""


def caps() -> dict:
    """The spend caps this leg runs under (written into every plan/decision for the receipt)."""
    return {"baseten_round_hard_usd": BASETEN_ROUND_CAP_USD,
            "meta_soft_usd": META_SOFT_CAP_USD, "meta_hard_usd": META_HARD_CAP_USD,
            "baseten_key_env": BASETEN_KEY_ENV, "modal": "from the $100 Modal credit"}


def _baseten_spend(results: dict) -> float:
    """Total recorded Baseten USD across classes in a same-prompt results file."""
    row = results.get("baseten") or {}
    return round(sum(float((c or {}).get("usd") or 0.0) for c in row.values()), 4)


def enforce_caps(results: dict) -> dict:
    """Hard-stop check on a real results file: refuse to report if Baseten went past its round cap."""
    spent = _baseten_spend(results)
    if spent > BASETEN_ROUND_CAP_USD:
        raise CapExceeded(f"Baseten round spent ${spent} > hard cap ${BASETEN_ROUND_CAP_USD}; "
                          "stopped (raise the cap only with Akash's go)")
    return {"baseten_usd": spent, "baseten_round_hard_usd": BASETEN_ROUND_CAP_USD,
            "within_cap": True}

# ESTIMATE inputs for the plan. Sources in each note.
BASETEN_USD_PER_1K = 1.863593      # MEASURED on 28 Oct (reports/hosted-ab/live-20261028.json)
MODAL_A100_USD_PER_H = 2.50        # ESTIMATE: list price class for one A100 80GB, check before go
MODAL_COLD_START_MIN = 6           # ESTIMATE: image pull + vLLM load seen on 31 Oct
MODAL_S_PER_ITEM = 25              # ESTIMATE: ~512 tokens at 27 tok/s (MEASURED 31 Oct) incl. thinking off


def plan(items_per_class: int, reps: int) -> dict:
    calls = items_per_class * len(CLASSES) * reps
    modal_h = (MODAL_COLD_START_MIN * 60 + calls * MODAL_S_PER_ITEM) / 3600
    baseten_usd = round(calls / 1000 * BASETEN_USD_PER_1K * 1.5, 2)
    return {
        "spec": "wattnot.multiprovider-plan/1", "label": "ESTIMATE",
        "same_prompts": True, "classes": CLASSES, "items_per_class": items_per_class, "reps": reps,
        "calls_per_provider": calls,
        "providers": {
            "h100_e2e": {"usd": 0.0, "note": "inside the E2E pass time already booked",
                         "energy": "MEASURED (NVML)"},
            "baseten": {"usd": baseten_usd,
                        "note": f"1.5x the MEASURED $/1k answers for longer prompts; guard: "
                                f"hosted_ab --cap-usd {BASETEN_ROUND_CAP_USD:g} "
                                f"(hard stop); key {BASETEN_KEY_ENV} only",
                        "over_round_cap": baseten_usd > BASETEN_ROUND_CAP_USD,
                        "energy": "ESTIMATE (no GPU counters)"},
            "modal": {"usd": round(modal_h * MODAL_A100_USD_PER_H, 2), "gpu_h": round(modal_h, 2),
                      "note": "from the $100 Modal credit; scale-to-zero after", "energy":
                      "MEASURED (NVML on the Modal A100)"},
        },
        "caps": caps(),
        "needs_go": True,
        "live_commands_not_run": [
            f"{BASETEN_KEY_ENV}=... python -m wattnot.spark_open.hosted_ab --live --hosts baseten "
            f"--cap-usd {BASETEN_ROUND_CAP_USD:g} ...   # hard stop at ${BASETEN_ROUND_CAP_USD:g}/round",
            "modal run modal/proof_cpu30.py  (same prompts, our Glimmer, thinking off)",
            f"# grading (Meta): soft cap ${META_SOFT_CAP_USD:g}, hard cap ${META_HARD_CAP_USD:g} "
            "(enforced by the Meta spend ledger)",
        ],
    }


def decide(results: dict) -> dict:
    """results[provider][class] = {correct, n, j (or None), usd}. Pick per class."""
    out = {}
    for cls in CLASSES:
        rows = {p: r[cls] for p, r in results.items() if cls in r and r[cls].get("n")}
        if not rows:
            continue
        pct = {p: 100 * r["correct"] / r["n"] for p, r in rows.items()}
        best = max(pct.values())
        ok = {p: r for p, r in rows.items() if pct[p] >= best - GATE_PTS and r["correct"]}
        use_j = all(r.get("j") is not None for r in ok.values())
        key = "j_per_correct" if use_j else "usd_per_correct"

        def score(r):
            return (r["j"] if use_j else r["usd"]) / r["correct"]
        pick = min(ok, key=lambda p: score(ok[p])) if ok else None
        out[cls] = {"pass_pct": pct, "gate_passed": sorted(ok), "by": key,
                    "scores": {p: round(score(r), 4) for p, r in ok.items()}, "route_to": pick}
    return out


def replay() -> dict:
    """Worked example from recorded runs. Different prompts per provider: NOT a same-prompt test."""
    rr = json.loads((ROOT / "data/h100-rerun-1031/public_bench_summary.json").read_text())
    modal = json.loads((ROOT / "data/modal-live-1031/results.json").read_text())
    h100_usd_per_s = 451.2 / 88.0 / 3600     # E2E list price incl. GST (INR) at 88 INR/USD ASSUMED
    res: dict = {"h100_e2e": {}, "baseten": {}}
    for name, cls in (("gsm8k", "gsm8k300"), ("humaneval", "humaneval164")):
        c = (rr.get("sets") or {}).get(name, {}).get("cap") or {}
        if c.get("pass_pct") is None:
            continue
        n = 100   # per 100 requests
        res["h100_e2e"][cls] = {"n": n, "correct": round(c["pass_pct"]), "j": c["j_per_req"] * n,
                                "usd": n * c.get("p50_s", 10) * h100_usd_per_s}
        # Baseten: MEASURED pass rate and $ on its own prompts (28 Oct), J is not measurable there
        res["baseten"][cls] = {"n": n, "correct": 100, "j": None,
                               "usd": n / 1000 * BASETEN_USD_PER_1K}
    return {"spec": "wattnot.multiprovider-route/1", "label": "REPLAY",
            "warning": "recorded runs, different prompts per provider; not a same-prompt result",
            "modal": {"j_per_request_measured": modal.get("mean_j_per_request"),
                      "excluded": "answers were not graded on 31 Oct (all hit max_tokens), so "
                                  "Modal cannot pass the quality gate in this replay"},
            "baseten_usd_per_1k_measured": BASETEN_USD_PER_1K,
            "decision": decide(res) if any(res.values()) else {},
            "inputs": res}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--replay", action="store_true")
    g.add_argument("--results")
    ap.add_argument("--items-per-class", type=int, default=20)
    ap.add_argument("--reps", type=int, default=2)
    a = ap.parse_args(argv)
    if a.results:
        res = json.loads(Path(a.results).read_text())
        try:
            cap_check = enforce_caps(res["providers"])
        except CapExceeded as e:
            # a real round that blew the Baseten hard cap is a hard stop: report it, do not decide
            doc = {"spec": "wattnot.multiprovider-route/1", "label": res.get("label", "MEASURED"),
                   "caps": caps(), "hard_stop": str(e), "inputs": res["providers"]}
            out = Path(a.out)
            out.mkdir(parents=True, exist_ok=True)
            (out / "multiprovider.json").write_text(json.dumps(doc, indent=2))
            print("HARD STOP:", e)
            return 1
        doc = {"spec": "wattnot.multiprovider-route/1", "label": res.get("label", "MEASURED"),
               "caps": caps(), "cap_check": cap_check,
               "decision": decide(res["providers"]), "inputs": res["providers"]}
    elif a.replay:
        doc = replay()
    else:
        doc = plan(a.items_per_class, a.reps)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "multiprovider.json").write_text(json.dumps(doc, indent=2))
    print(json.dumps(doc)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
