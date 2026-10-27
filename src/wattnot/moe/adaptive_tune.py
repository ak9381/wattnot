"""Choosing the adaptive top-k `mass` from CPU agreement numbers (pure functions, no torch).

`scripts/tune_adaptive_mass.py` measures, on a CPU, how often adaptive top-k picks the same next
token as normal top-k at each `mass`, and how many experts it keeps. This module turns those
tables into one choice and a saving estimate.

The rule: the smallest `mass` where, on EVERY prompt set, the agreement is at least the gate plus
a margin and the lower end of its 95% interval is still at least the gate. The margin is there
because the run on the GPU measures only 512 to 1,700 positions, so its own reading wobbles by a
point or more around the true rate.

The saving is an ESTIMATE, never a measurement: RUN2 measured one point (14.9% fewer expert slots
gave -6.6% J/token on the H100), and this scales that point to the new expert cut. Energy per
token is not linear in experts (attention, the router and the hook cost the same), so treat the
number as an upper-ish guide, and measure it on the GPU before quoting it.
"""

from __future__ import annotations

GATE_PCT = 95.0
MARGIN_PTS = 1.0


def expert_cut_pct(active_experts: float, top_k: int) -> float:
    """Share of the top-k expert slots adaptive top-k skips, in percent."""
    return 100.0 * (1.0 - active_experts / top_k)


def estimate_saving_pct(cut_pct: float, ref_cut_pct: float, ref_change_pct: float) -> float:
    """ESTIMATE of the J/token change (negative = saving): the reference point's change, scaled
    in proportion to the expert cut."""
    return ref_change_pct * (cut_pct / ref_cut_pct) if ref_cut_pct else 0.0


def pick_mass(sets: dict, *, gate: float = GATE_PCT, margin: float = MARGIN_PTS) -> dict:
    """`sets` is the `sets` table of tune.json: name -> {"by_mass": {mass: {agreement_pct, ci95,
    active_experts, positions}}}. Returns {"mass", "per_set"} for the smallest mass that clears
    every set, or {"mass": None, "why": ...}."""
    masses = sorted({float(m) for st in sets.values() for m in st["by_mass"]})
    names = sorted(sets)
    for m in masses:
        key = f"{m:g}"
        if not all(key in sets[n]["by_mass"] for n in names):
            continue
        rows = {n: sets[n]["by_mass"][key] for n in names}
        if all(r["agreement_pct"] >= gate + margin and r["ci95"][0] >= gate for r in rows.values()):
            return {
                "mass": m,
                "per_set": {
                    n: {
                        "agreement_pct": round(r["agreement_pct"], 2),
                        "ci95_low": round(r["ci95"][0], 2),
                        "positions": r["positions"],
                        "active_experts": round(r["active_experts"], 3),
                    }
                    for n, r in rows.items()
                },
            }
    return {"mass": None, "why": f"no mass clears {gate + margin:g}% on every set"}


def summary_rows(sets: dict, top_k: int, ref: dict) -> list[dict]:
    """One row per mass: worst-set agreement, mean experts kept, expert cut, saving ESTIMATE."""
    ref_cut = expert_cut_pct(ref["active_experts"], top_k)
    rows = []
    for key in sorted({m for st in sets.values() for m in st["by_mass"]}, key=float):
        if not all(key in st["by_mass"] for st in sets.values()):
            continue
        per = [st["by_mass"][key] for st in sets.values()]
        pos = sum(r["positions"] for r in per)
        act = sum(r["active_experts"] * r["positions"] for r in per) / pos
        cut = expert_cut_pct(act, top_k)
        rows.append({
            "mass": float(key),
            "worst_set_agreement_pct": round(min(r["agreement_pct"] for r in per), 2),
            "worst_set_ci95_low": round(min(r["ci95"][0] for r in per), 2),
            "active_experts": round(act, 3),
            "expert_cut_pct": round(cut, 2),
            "saving_estimate_pct": round(
                estimate_saving_pct(cut, ref_cut, ref["j_per_token_change_pct"]), 2
            ),
            "label": "ESTIMATE",
        })
    return rows
