"""Pick where a workload runs: our own GPU, Baseten hosted, or Modal hosted.

The head-to-head (h2h.py) measures both. This module turns those rows into a choice per
workload and writes the choice, with the numbers behind it, into the receipt (`routing`).
Order of the decision, fixed so the same inputs always give the same answer:

1. Gate. A backend is out if it made no answers, had failed calls, or scored more than
   1 point below the better backend on the fixed checks. A private workload may not
   leave the box, so Baseten is out for it, however cheap or fast.
2. Interactive (a person is waiting): the backend whose p99 latency fits the budget wins;
   among those, the lowest p99. Within 10% on p99, the cheaper per 1k answers wins.
   If nobody fits the budget, the faster one is chosen and the receipt says the budget was missed.
3. Batch (nobody is waiting): the lowest cost per 1k answers wins. Within 3% on cost, the
   lower energy per answer wins. Energy is MEASURED only for local; Baseten's is an
   ESTIMATE, so it can break a tie but never decides alone.
4. Still tied: local wins (we own it and can measure it), then the id.

The router never trusts a number without its label. Every candidate row in the receipt
carries the label of each figure it used.

    python -m wattnot.spark_open.router --h2h out/h2h.json --workload batch
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from wattnot.receipt.canon import canonical_sha256

QUALITY_SLACK_PTS = 1.0
P99_TIE_PCT = 10.0
COST_TIE_PCT = 3.0
DEFAULT_P99_BUDGET_S = 20.0


@dataclass(frozen=True)
class WorkloadProfile:
    kind: str  # interactive | batch
    private: bool = False  # data must stay on our box
    p99_budget_s: float | None = None
    name: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ("interactive", "batch"):
            raise ValueError("kind must be interactive or batch")

    @property
    def label(self) -> str:
        return self.name or f"{self.kind}{'+private' if self.private else ''}"


DEFAULT_PROFILES = (
    WorkloadProfile("interactive"),
    WorkloadProfile("batch"),
    WorkloadProfile("batch", private=True),
)


def _row(name: str, leg: Mapping) -> dict:
    e, c, lat, q = (
        leg["energy_per_answer"],
        leg["cost_per_1k_answers"],
        leg["latency_s"],
        leg["quality"],
    )
    return {
        "id": name,
        "pass_pct": q["pass_pct"],
        "pass_label": q["label"],
        "p99_s": lat["p99"],
        "latency_label": lat["label"],
        "usd_per_1k": c["usd"],
        "cost_label": c["label"],
        "wh_per_answer": e["wh"],
        "energy_label": e["label"],
        "answers": leg["answers"],
        "failed_calls": leg["failed_calls"],
    }


def route(profile: WorkloadProfile, legs: Mapping[str, Mapping]) -> dict:
    """Decide for one workload. `legs` is the `legs` block of an h2h receipt."""

    rows = {n: _row(n, leg) for n, leg in legs.items()}
    best_q = max((r["pass_pct"] for r in rows.values() if r["answers"]), default=0.0)
    out: list[dict] = []
    ok: list[dict] = []
    for n in sorted(rows):
        r = dict(rows[n])
        why = None
        if r["answers"] == 0:
            why = "made no answers"
        elif r["failed_calls"]:
            why = f"{r['failed_calls']} failed call(s)"
        elif r["pass_pct"] < best_q - QUALITY_SLACK_PTS:
            why = (
                f"quality {r['pass_pct']}% is more than {QUALITY_SLACK_PTS:g} point below "
                f"the best ({best_q}%)"
            )
        elif profile.private and n != "local":
            why = "private workload: data may not leave our box"
        r["status"], r["why"] = ("excluded", why) if why else ("passed", "")
        out.append(r)
        if not why:
            ok.append(r)
    budget = profile.p99_budget_s or DEFAULT_P99_BUDGET_S
    rule, reason, chosen, flags = "", "", None, []
    if not ok:
        rule, reason = "gate", "no backend passed the gate, so nothing is routed"
    elif profile.kind == "interactive":
        rule = "interactive: p99 inside the budget, then lowest p99, then cheaper"
        fit = [r for r in ok if r["p99_s"] is not None and r["p99_s"] <= budget]
        pool = fit or ok
        if not fit:
            flags.append(f"p99 budget of {budget:g} s missed by every candidate")
        best = min(r["p99_s"] for r in pool)
        band = [r for r in pool if r["p99_s"] <= best * (1 + P99_TIE_PCT / 100)]
        band.sort(
            key=lambda r: (
                r["usd_per_1k"] if r["usd_per_1k"] is not None else float("inf"),
                r["id"] != "local",
                r["id"],
            )
        )
        chosen = band[0]
        reason = (
            f"{chosen['id']}: p99 {chosen['p99_s']} s "
            f"({'inside' if fit else 'over'} the {budget:g} s budget)"
        )
        if len(band) > 1:
            reason += (
                f"; {len(band)} were within {P99_TIE_PCT:g}% on p99, "
                "so the cheaper per 1k answers won"
            )
    else:
        rule = "batch: lowest USD per 1k answers, then lower energy, then local"
        costed = [r for r in ok if r["usd_per_1k"] is not None]
        pool = costed or ok
        best = min((r["usd_per_1k"] for r in pool if r["usd_per_1k"] is not None), default=None)
        band = (
            pool
            if best is None
            else [r for r in pool if r["usd_per_1k"] <= best * (1 + COST_TIE_PCT / 100)]
        )
        band.sort(
            key=lambda r: (
                r["wh_per_answer"] if r["wh_per_answer"] is not None else float("inf"),
                r["id"] != "local",
                r["id"],
            )
        )
        chosen = band[0]
        reason = f"{chosen['id']}: ${chosen['usd_per_1k']} per 1k answers"
        if len(pool) > 1 and len(band) == 1:
            other = next(r for r in pool if r is not chosen)
            reason += f" against ${other['usd_per_1k']} for {other['id']}"
        if len(band) > 1:
            reason += (
                f"; within {COST_TIE_PCT:g}% on cost, so lower energy per answer decided "
                f"({chosen['wh_per_answer']} Wh, {chosen['energy_label']})"
            )
    for r in out:
        if chosen is not None and r["id"] == chosen["id"]:
            r["status"] = "chosen"
    if profile.private and "baseten" in rows:
        flags.append("Baseten excluded: private workload")
    return {
        "workload": {
            "kind": profile.kind,
            "private": profile.private,
            "p99_budget_s": budget if profile.kind == "interactive" else None,
            "name": profile.label,
        },
        "rule": rule,
        "chosen": None if chosen is None else chosen["id"],
        "reason": reason,
        "flags": flags,
        "candidates": out,
        "thresholds": {
            "quality_slack_pts": QUALITY_SLACK_PTS,
            "p99_tie_pct": P99_TIE_PCT,
            "cost_tie_pct": COST_TIE_PCT,
        },
    }


def attach(receipt: dict, profiles=DEFAULT_PROFILES) -> dict:
    """Write the routing choices into an h2h receipt and re-hash it. The chain link stays."""

    decisions = [route(p, receipt["legs"]) for p in profiles]
    receipt["routing"] = {
        "spec": "wattnot.spark-route/1",
        "decisions": decisions,
        "how": "the run measured both backends on the same prompts, then chose per workload",
    }
    if isinstance(receipt.get("escalation"), dict):  # small-first: Llama 8B, Spark only on a gate fail
        from wattnot.route.escalate import router_row

        row = router_row(receipt["escalation"])
        if row:
            receipt["routing"]["small_first"] = row
    integ = dict(receipt.get("integrity") or {})
    integ.update(canonicalization="JCS RFC 8785", sha256=canonical_sha256(receipt))
    integ.setdefault("prev_sha256", None)
    receipt["integrity"] = integ
    return receipt


def verify(receipt: Mapping) -> bool:
    return (receipt.get("integrity") or {}).get("sha256") == canonical_sha256(receipt)


def main(argv=None) -> int:  # pragma: no cover - thin CLI
    ap = argparse.ArgumentParser(prog="wattnot.spark_open.router")
    ap.add_argument("--h2h", required=True, help="path to h2h.json (rewritten with routing)")
    ap.add_argument("--workload", choices=("interactive", "batch", "all"), default="all")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--p99-budget-s", type=float)
    a = ap.parse_args(argv)
    p = Path(a.h2h)
    r = json.loads(p.read_text())
    profiles = (
        DEFAULT_PROFILES
        if a.workload == "all"
        else (WorkloadProfile(a.workload, a.private, a.p99_budget_s),)
    )
    attach(r, profiles)
    p.write_text(json.dumps(r, indent=2) + "\n")
    for d in r["routing"]["decisions"]:
        print(f"{d['workload']['name']:>18} -> {d['chosen']}  ({d['reason']})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
