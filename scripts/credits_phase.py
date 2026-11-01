"""Credits phase: spend the Modal and Baseten balances down to a 5% margin (plan + dry run).

Nothing here makes a paid call. `--dry-run` simulates every block against its spend-guard cap
and runs the one-command flow off-GPU per pass (REPLAY data), so all numbers are SIMULATED or
ESTIMATE. A live run needs Akash's go and is not wired to this script on purpose.

  python scripts/credits_phase.py --modal-usd 99.57 --baseten-usd 50 --out /tmp/credits --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

MARGIN = 0.05
DATASETS = ("own", "gsm8k300", "humaneval164")

# Modal rates read with `modal billing rates` on 2026-11-01 (MEASURED list price).
MODAL_H100_H = 3.95
MODAL_A100_80_H = 2.50
MODAL_CPU_CORE_H = 0.0473
MODAL_MEM_GIB_H = 0.008
MODAL_CORES, MODAL_MEM_GIB = 8, 64
MODAL_HOST_H = MODAL_CORES * MODAL_CPU_CORE_H + MODAL_MEM_GIB * MODAL_MEM_GIB_H  # ~0.89 $/h
MODAL_H100_ALLIN_H = MODAL_H100_H + MODAL_HOST_H                                # ~4.84 $/h

# Baseten Muse Spark Open: MEASURED $1.8636 per 1k answers on short prompts; 1.5x for the
# longer agent and benchmark prompts (ESTIMATE).
BASETEN_PER_CALL = 1.5 * 1.8636 / 1000

# One Modal H100 pass (minutes). Power cap and clock lock are probably not allowed in a
# Modal container: the sweep block tries `nvidia-smi -pl` and, if refused, records
# "not permitted on host" and runs uncapped repeat rounds instead (never faked).
MODAL_PASS_MIN = {"cold-start+load": 18, "sweep-or-repeats": 50, "think": 15, "tiers": 25,
                  "eye": 3, "spoken": 3, "langchain": 25, "agentwaste": 20, "power-repeats": 30}

# One Baseten pass (calls). No GPU counters or knobs: quality, cost and routing layers only.
BASETEN_PASS_CALLS = {"langchain (40 items x 2 arms x ~3 steps)": 240, "tiers (3 x 50)": 150,
                      "cloud effort tune (4 levels x 100)": 400,
                      "quality, dataset x 3 reps": 900}

MP_ITEMS, MP_CLASSES, MP_REPS = 100, 3, 3   # multi-provider at 5x the first plan's sample


def plan(modal_usd: float, baseten_usd: float | None) -> dict:
    blocks: list[dict] = []
    m_budget = round(modal_usd * (1 - MARGIN), 2)
    pass_h = sum(MODAL_PASS_MIN.values()) / 60
    pass_usd = round(pass_h * MODAL_H100_ALLIN_H, 2)
    mp_calls = MP_ITEMS * MP_CLASSES * MP_REPS
    fixed = [
        {"id": "m-mp", "provider": "modal", "title": f"multi-provider routing, Modal arm, "
         f"{mp_calls} calls (A100 80GB, ~2 h)", "usd": round(2 * (MODAL_A100_80_H + MODAL_HOST_H), 2)},
        {"id": "m-serverless", "provider": "modal", "title": "serverless: warm vs scale-to-zero, "
         "cold-start count, 10 rounds (H100, ~1 h incl. idle gaps)", "usd": round(MODAL_H100_ALLIN_H, 2)},
    ]
    blocks += fixed
    left = m_budget - sum(b["usd"] for b in fixed)
    n_modal = max(0, int(left // pass_usd))
    for i in range(n_modal):
        ds = DATASETS[i % 3]
        blocks.append({"id": f"m-p{i+1}", "provider": "modal", "dataset": ds, "hours": round(pass_h, 2),
                       "title": f"Modal H100 full pass {i+1} ({ds}, repeat {i // 3 + 1})", "usd": pass_usd})
    left -= n_modal * pass_usd
    if left >= MODAL_H100_ALLIN_H * 0.5:
        h = round(left / MODAL_H100_ALLIN_H - 0.005, 2)
        blocks.append({"id": "m-extra", "provider": "modal", "hours": h, "usd": round(h * MODAL_H100_ALLIN_H, 2),
                       "title": f"extra repeat rounds on the H100 ({h} h) for statistical power"})
    b_budget = None
    n_bt = 0
    if baseten_usd is not None:
        b_budget = round(baseten_usd * (1 - MARGIN), 2)
        mp = {"id": "b-mp", "provider": "baseten", "calls": mp_calls,
              "title": f"multi-provider routing, Baseten arm, {mp_calls} calls",
              "usd": round(mp_calls * BASETEN_PER_CALL, 2)}
        blocks.append(mp)
        calls = sum(BASETEN_PASS_CALLS.values())
        bp_usd = round(calls * BASETEN_PER_CALL, 2)
        n_bt = max(0, int((b_budget - mp["usd"]) // bp_usd))
        for i in range(n_bt):
            ds = DATASETS[i % 3]
            blocks.append({"id": f"b-p{i+1}", "provider": "baseten", "dataset": ds, "calls": calls,
                           "title": f"Baseten Muse Spark Open pass {i+1} ({ds}, repeat {i // 3 + 1})",
                           "usd": bp_usd})
    for b in blocks:
        b["cap_usd"] = round(b["usd"] * 1.10, 2)   # per-block guard; the provider total cap still binds
    tot = lambda p: round(sum(b["usd"] for b in blocks if b["provider"] == p), 2)  # noqa: E731
    return {"spec": "wattnot.credits-phase-plan/1", "label": "ESTIMATE", "margin": MARGIN, "needs_go": True,
            "modal": {"balance_usd": modal_usd, "budget_usd": m_budget, "total_usd": tot("modal"),
                      "passes": n_modal, "pass_hours": round(pass_h, 2), "pass_usd": pass_usd,
                      "h100_allin_per_h": round(MODAL_H100_ALLIN_H, 3), "total_cap_usd": m_budget},
            "baseten": {"balance_usd": baseten_usd, "budget_usd": b_budget, "total_usd": tot("baseten"),
                        "passes": n_bt, "per_call_usd": round(BASETEN_PER_CALL, 6),
                        "total_cap_usd": b_budget},
            "blocks": blocks}


def dry_run(p: dict, out: Path, flow: bool = True) -> dict:
    """SIMULATED: each block 'spends' its estimate against the guards; passes run the flow off-GPU."""
    spent = {"modal": 0.0, "baseten": 0.0}
    rows = []
    env = dict(os.environ, WATTNOT_META_MOCK="1")
    for b in p["blocks"]:
        prov = b["provider"]
        ok = b["usd"] <= b["cap_usd"] and spent[prov] + b["usd"] <= p[prov]["total_cap_usd"] + 1e-9
        rc = None
        if ok and flow and "dataset" in b:
            rc = subprocess.run([sys.executable, "-m", "wattnot.flow", "--out", str(out / "flow" / b["id"]),
                                 "--dataset", b["dataset"], "--tag", b["id"]], env=env,
                                capture_output=True).returncode
            ok = rc == 0
        if ok:
            spent[prov] = round(spent[prov] + b["usd"], 2)
        rows.append({"id": b["id"], "ok": ok, "flow_rc": rc, "usd": b["usd"], "cum": spent[prov]})
    return {"label": "SIMULATED", "rows": rows, "spent": spent, "all_ok": all(r["ok"] for r in rows)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modal-usd", type=float, required=True)
    ap.add_argument("--baseten-usd", type=float, default=None, help="unknown -> Baseten not planned")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-flow", action="store_true", help="dry run without the off-GPU flow per pass")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    p = plan(a.modal_usd, a.baseten_usd)
    if a.dry_run:
        p["dry_run"] = dry_run(p, out, flow=not a.no_flow)
    (out / "credits_phase.json").write_text(json.dumps(p, indent=1))
    for b in p["blocks"]:
        print(f"{b['id']:12} {b['provider']:8} ${b['usd']:7.2f} cap ${b['cap_usd']:7.2f}  {b['title']}")
    print("modal", {k: p["modal"][k] for k in ("budget_usd", "total_usd", "passes")},
          "baseten", {k: p["baseten"][k] for k in ("budget_usd", "total_usd", "passes")})
    if a.dry_run:
        print("dry run (SIMULATED):", "all ok" if p["dry_run"]["all_ok"] else "FAILED", p["dry_run"]["spent"])
        return 0 if p["dry_run"]["all_ok"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
