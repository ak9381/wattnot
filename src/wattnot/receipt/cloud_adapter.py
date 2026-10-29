"""Turn a cloud receipt (`wattnot.cloud-receipt/1`) into a spec receipt (`wattnot.receipt/1`).

A hosted model gives us tokens, dollars and latency from the API, but never a power
meter. So the adapted receipt says what it knows and what it does not:

* baseline = the same requests at `xhigh` (the API default), candidate = the tuned effort.
* `wh_per_req` is the middle of the ESTIMATE range, and the range is kept next to it.
  `measurement.label` is ESTIMATE, or SIMULATED for a synthetic replay.
* There is no significance test across repeats, so `result.welch.applicable` is false.
  Such a receipt can never be VALID: the best grade is ESTIMATE.
* Nothing is billed. `billable` is false and `billed_saving_pct` is 0.
* The original receipt is pinned by its sha256 in the `cloud` block.

The result carries the `joules` block, so a hosted run and a GPU run can be compared on
joules per good answer. It is a new document: it needs its own signature.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from wattnot.cloud import receipt as _cloud
from wattnot.receipt.build import WATER_METHOD, WUE_L_PER_KWH, WUE_UPPER_L_PER_KWH
from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.grade import SPEC, claimed_grade
from wattnot.receipt.joules import with_joules
from wattnot.receipt.lint import lint
from wattnot.schemas import validate_receipt

CLOUD_SCHEMA = "wattnot.cloud-receipt/1"
HOSTED_WATER_SOURCE = ("0.36 L/kWh on-site cooling (LBNL 2024) applied to an ESTIMATE of site "
                       "energy; ESTIMATE, not measured")
HOSTED_WATER_SOURCE_UPPER = ("1.8 L/kWh including power-plant water applied to an ESTIMATE of site "
                             "energy; upper ESTIMATE, not measured")


def _pass_rates(classes: list[Mapping]) -> tuple[float, float]:
    """Request-weighted pass rate for (xhigh baseline, chosen effort), as 0..1."""

    n = base = cand = 0.0
    for c in classes:
        w = c["items"] * c["repeats"]
        tried = [t for t in c["tried"] if not t.get("skipped")]
        ref = next((t["reference_pct"] for t in tried if "reference_pct" in t), None)
        chosen = next((t["quality_pct"] for t in tried if t["effort"] == c["chosen_effort"]
                       and "quality_pct" in t), None)
        if ref is None:
            ref = 100.0  # the reference is the standard its own gate measures against
        if chosen is None:
            chosen = ref  # stayed on xhigh: same answers as the reference
        n += w
        base += w * ref / 100
        cand += w * chosen / 100
    if not n:
        raise ValueError("cloud receipt has no requests")
    return round(base / n, 6), round(cand / n, 6)


def _short(s: str) -> str:
    return s if len(s) <= 200 else s[:197] + "..."


def from_cloud(cloud: Mapping, *, prev_sha256: str | None = None) -> dict[str, Any]:
    """Build the spec receipt. Raises ValueError if the cloud receipt is not intact."""

    if cloud.get("schema") != CLOUD_SCHEMA:
        raise ValueError(f"not a cloud receipt: schema is {cloud.get('schema')!r}")
    ok, want, got = _cloud.verify_hash(cloud)
    if not ok:
        raise ValueError(f"cloud receipt hash does not match its content ({want[:12]} vs "
                         f"{got[:12]}); refusing to adapt a changed receipt")
    simulated = cloud["label"] == "SIMULATED"
    label = "SIMULATED" if simulated else "ESTIMATE"
    classes = cloud["classes"]
    n_req = cloud["totals"]["requests"]
    e = cloud["energy"]
    t_lo, t_hi = e["tuned_wh"]["low"] / n_req, e["tuned_wh"]["high"] / n_req
    b_lo, b_hi = e["baseline_wh"]["low"] / n_req, e["baseline_wh"]["high"] / n_req
    t_mid, b_mid = (t_lo + t_hi) / 2, (b_lo + b_hi) / 2
    base_pass, cand_pass = _pass_rates(classes)
    tot = cloud["totals"]

    def p99(key: str) -> float:
        return round(1000 * max(c[key]["latency_p99_s"] for c in classes), 3)

    def arm(effort: str, mid: float, lo: float, hi: float, usd: float, p: float,
            p99_ms: float) -> dict:
        return {"params": {"reasoning_effort": effort}, "reps": int(cloud["settings"].get(
                    "repeats", 1)),
                "wh_per_req": {"mean": round(mid, 9), "sd": 0.0,
                               "range": {"low": round(lo, 9), "high": round(hi, 9)},
                               "basis": "middle of an ESTIMATE range, not a measurement"},
                "usd_per_req": {"mean": round(usd, 10), "sd": 0.0},
                "p99_ms": p99_ms, "quality": {"fixed_pass_rate": p}}

    tuned_classes = {c["class"]: c["chosen_effort"] for c in classes}
    r: dict[str, Any] = {
        "spec": SPEC,
        "profile": "cloud",
        "receipt_id": "rcpt_" + cloud["receipt_id"].removeprefix("cloud_"),
        "created_at": cloud["created_at"],
        "run_id": cloud["receipt_id"],
        "subject": {"model": cloud["model"],
                    "host": {"provider": cloud["provider"], "gpu": "unknown (hosted)",
                             "driver": "n/a"},
                    "mode": "cloud", "workload": dict(cloud["workload"])},
        "baseline": arm("xhigh", b_mid, b_lo, b_hi, tot["xhigh_usd_per_request"], base_pass,
                        p99("would_have_cost_xhigh")),
        "candidate": arm("tuned", t_mid, t_lo, t_hi, tot["tuned_usd_per_request"], cand_pass,
                         p99("measured")),
        "result": {
            "saving_pct": {"mean": round(100 * (b_mid - t_mid) / b_mid, 4) if b_mid else 0.0,
                           "ci_low": None, "ci_high": None,
                           "basis": "ESTIMATE: ratio of two FLOP-model energies"},
            "welch": {"applicable": False,
                      "reason": "hosted run, no repeated energy samples to test"},
            "billable": False, "billed_saving_pct": 0.0, "fee_share": 0.0, "currency": "USD",
            "usd_saving_pct": {"value": tot["saving_pct"], "label": cloud["labels"]["usd"]},
        },
        "measurement": {"meter": "baseten usage block (tokens) + FLOP model (energy)",
                        "hz": 0, "csv": [], "label": label},
        "cloud": {
            "source_receipt": {"schema": CLOUD_SCHEMA, "receipt_id": cloud["receipt_id"],
                               "sha256": cloud["integrity"]["sha256"],
                               "mode": cloud["mode"]},
            "labels": dict(cloud["labels"]),
            "chosen_effort": tuned_classes,
            "tuned_usd": tot["tuned_usd"],
            "would_have_cost_xhigh_usd": tot["would_have_cost_xhigh_usd"],
            "saving_usd": tot["saving_usd"],
            "requests": n_req,
            "energy_wh_range": {"tuned": dict(e["tuned_wh"]), "baseline": dict(e["baseline_wh"]),
                                "label": "ESTIMATE" if not simulated else "SIMULATED"},
            "unverified": [_short(x) for x in cloud.get("unverified", [])],
        },
        "planner_trace": [],
        "estimates": {
            "water_ml_per_req": {"value": round(t_mid * WUE_L_PER_KWH, 6), "label": "ESTIMATE",
                                 "method": WATER_METHOD, "source": HOSTED_WATER_SOURCE},
            "water_ml_per_req_upper": {"value": round(t_mid * WUE_UPPER_L_PER_KWH, 6),
                                       "label": "ESTIMATE", "method": WATER_METHOD,
                                       "source": HOSTED_WATER_SOURCE_UPPER},
            "baseline_water_ml_per_req": {"value": round(b_mid * WUE_L_PER_KWH, 6),
                                          "label": "ESTIMATE", "method": WATER_METHOD,
                                          "source": HOSTED_WATER_SOURCE},
            "baseline_water_ml_per_req_upper": {"value": round(b_mid * WUE_UPPER_L_PER_KWH, 6),
                                                "label": "ESTIMATE", "method": WATER_METHOD,
                                                "source": HOSTED_WATER_SOURCE_UPPER}},
    }
    with_joules(r)
    r["grade"] = claimed_grade(r, rekor=False)
    r["integrity"] = {"canonicalization": "JCS RFC 8785", "sha256": canonical_sha256(r),
                      "prev_sha256": prev_sha256, "signer": None, "rekor": None}
    problems = lint(r) + [f"banned wording: {w}" for w in _cloud.BANNED
                          if w in str(r).lower()]
    if simulated and "MEASURED" in str(r):
        problems.append("a SIMULATED receipt must not say MEASURED")
    if problems:
        raise ValueError("adapted receipt failed checks: " + "; ".join(problems))
    validate_receipt(r)
    return r
