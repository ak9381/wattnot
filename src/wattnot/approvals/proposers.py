"""Turn what WattNot already produces (receipts, gate decisions, grid events) into proposals.

Every proposal says what changes, the saving with its label, the quality and p99 check
results, and a preview of the receipt that will back it. Nothing here applies anything.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from wattnot.approvals import model as m

PARAM_KEYS = ("max_num_seqs", "enable_chunked_prefill", "enable_prefix_caching", "block_size",
              "quantization", "gpu_mem_util")


def _round(x: float | None, n: int = 2) -> float | None:
    return None if x is None else round(float(x), n)


def checks_from_gates(results: Iterable[Any]) -> dict:
    """Quality and p99 check blocks from `GateResult`s (gate names fixed / p99 / noise)."""
    out: dict = {}
    for r in results:
        if r.gate == "fixed":
            out["quality"] = {"passed": bool(r.passed), "detail": r.reason,
                              "gate": "fixed checks vs baseline outputs"}
        elif r.gate == "p99":
            d = r.detail or {}
            b, c = d.get("baseline_p99_s"), d.get("candidate_p99_s")
            inc = None if not b or c is None else (c - b) / b * 100
            out["p99"] = {"passed": bool(r.passed), "detail": r.reason,
                          "baseline_p99_s": b, "candidate_p99_s": c, "budget_s": d.get("budget_s"),
                          "increase_pct": _round(inc, 1)}
        elif r.gate == "noise":
            out["noise"] = {"passed": bool(r.passed), "detail": r.reason}
    return out


def physical_check(fleet: Any, gpu: int, cap_w: float | None = None) -> dict:
    """The `checks.physical` block for a change to one GPU, from the rack photo audit.

    passed is True only when SAM outlined a unit for that GPU and the cap is not above the
    rack's per-node ceiling. Anything else is False, with the reason in `detail`.
    """

    lim = fleet.limit_for(gpu)
    if not lim.eligible:
        return {"passed": False, "detail": lim.reason, "gpu": gpu, "rack": lim.rack,
                "source": "sam-3.1 photo audit", "label": "MEASURED"}
    over = cap_w is not None and cap_w > lim.ceiling_w
    detail = (f"{cap_w:g} W is above the rack's per-node ceiling of {lim.ceiling_w} W"
              if over else f"{lim.reason}; per-node ceiling {lim.ceiling_w} W (ESTIMATE)")
    return {"passed": not over, "detail": detail, "gpu": gpu, "rack": lim.rack,
            "ceiling_w": lim.ceiling_w, "source": "sam-3.1 photo audit", "label": "MEASURED"}


def with_physical_check(spec: Mapping, fleet: Any) -> dict:
    """A copy of a power_cap spec with `checks.physical` filled in. Other kinds pass through."""

    out = {**spec, "checks": dict(spec.get("checks") or {})}
    ch = spec.get("change") or {}
    if spec.get("kind") == m.POWER_CAP and "gpu" in ch:
        cap = (ch.get("after") or {}).get("power_cap_w")
        out["checks"]["physical"] = physical_check(fleet, int(ch["gpu"]), cap)
    return out


def _preview(profile: str, label: str, *, grade: str | None = None, receipt: Mapping | None = None,
             will_claim: list[str], not_claimed: list[str]) -> dict:
    prev: dict = {"profile": profile, "label": label,
                  "expected_grade": grade, "will_claim": will_claim,
                  "not_claimed": not_claimed}
    if receipt is not None:  # the evidence receipt already exists: show where it is
        integ = receipt.get("integrity") or {}
        prev["evidence_receipt"] = {"receipt_id": receipt.get("receipt_id"),
                                    "sha256": integ.get("sha256"),
                                    "signed": bool(integ.get("signer")),
                                    "grade": receipt.get("grade")}
    return prev


def _arm_params(arm: Mapping) -> dict:
    return dict(arm.get("params") or {})


def from_gpu_receipt(receipt: Mapping, *, gates: Iterable[Any] = (), gpu: int = 0,
                     source: Mapping | None = None, ttl_s: float | None = None) -> list[dict]:
    """Proposals from a GPU compare receipt (baseline vs candidate).

    A candidate that changes the power cap gives a `power_cap` proposal; one that changes
    vLLM settings gives a `vllm_config` proposal; one that changes both gives both.
    `gates` are the gate results from the decision that went with the receipt; without them
    the quality and p99 checks are left empty, which blocks auto-approve.
    """
    label = (receipt.get("measurement") or {}).get("label") or "ESTIMATE"
    base, cand = _arm_params(receipt["baseline"]), _arm_params(receipt["candidate"])
    res = receipt.get("result") or {}
    pct = (res.get("saving_pct") or {})
    joules = receipt.get("joules") or {}
    jchg = (joules.get("change_pct") or {}).get("j_per_good_answer")
    b_wh = (receipt["baseline"].get("wh_per_req") or {}).get("mean")
    c_wh = (receipt["candidate"].get("wh_per_req") or {}).get("mean")
    savings: list[dict] = []
    if pct.get("mean") is not None:
        savings.append({"metric": "energy per request", "value": _round(pct["mean"]), "unit": "%",
                        "low": _round(pct.get("ci_low")), "high": _round(pct.get("ci_high")),
                        "label": label, "basis": "95% bootstrap interval across repeats"})
    if b_wh is not None and c_wh is not None:
        savings.append({"metric": "Wh per request", "value": _round(b_wh - c_wh, 4), "unit": "Wh",
                        "baseline": _round(b_wh, 4), "candidate": _round(c_wh, 4),
                        "label": label, "basis": "mean of repeats"})
    if jchg is not None:
        savings.append({"metric": "joules per good answer", "value": _round(-jchg),
                        "unit": "%", "label": joules.get("label") or label,
                        "basis": "energy per request divided by pass rate"})
    checks = checks_from_gates(gates)
    p99_ms = (receipt["baseline"].get("p99_ms"), receipt["candidate"].get("p99_ms"))
    if "p99" not in checks and all(p99_ms):
        inc = (p99_ms[1] - p99_ms[0]) / p99_ms[0] * 100
        checks["p99_observed"] = {"baseline_ms": p99_ms[0], "candidate_ms": p99_ms[1],
                                  "increase_pct": _round(inc, 1)}
    prev = _preview("gpu", label, grade=receipt.get("grade"), receipt=receipt,
                    will_claim=["energy per request, baseline vs candidate, with an interval",
                                "quality gate and p99 gate results"],
                    not_claimed=["no site or grid level saving", "no carbon claim"])
    src = {"tool": "wattnot gpu compare", "run_id": receipt.get("run_id"),
           "receipt_id": receipt.get("receipt_id"), **dict(source or {})}
    specs: list[dict] = []
    cap_b, cap_c = base.get("power_cap_w"), cand.get("power_cap_w")
    knob_b = {k: base[k] for k in PARAM_KEYS if k in base}
    knob_c = {k: cand[k] for k in PARAM_KEYS if k in cand}
    common = {"savings": savings, "checks": checks, "receipt_preview": prev, "source": src,
              **({"ttl_s": ttl_s} if ttl_s else {})}
    if cap_c is not None and cap_c != cap_b:
        drop = None if not cap_b else (cap_b - cap_c) / cap_b * 100
        specs.append({**common, "kind": m.POWER_CAP,
                      "title": f"Set GPU {gpu} power cap to {cap_c} W"
                               + (f" (from {cap_b} W)" if cap_b else ""),
                      "summary": f"Lower the power limit on GPU {gpu}"
                                 + (f" from {cap_b} W to {cap_c} W" if cap_b else f" to {cap_c} W")
                                 + f". Measured against the same workload; label {label}.",
                      "change": {"target": f"gpu:{gpu}", "gpu": gpu,
                                 "before": {"power_cap_w": cap_b}, "after": {"power_cap_w": cap_c},
                                 "drop_pct": _round(drop, 1)}})
    if knob_c != knob_b:
        diff = sorted(k for k in knob_c if knob_c.get(k) != knob_b.get(k))
        specs.append({**common, "kind": m.VLLM_CONFIG,
                      "title": "Change vLLM settings: " + ", ".join(
                          f"{k} {knob_b.get(k)} -> {knob_c[k]}" for k in diff),
                      "summary": "Restart vLLM with these settings (batch size, KV cache, "
                                 f"quantization). Measured against the baseline; label {label}.",
                      "change": {"target": "vllm", "before": knob_b, "after": knob_c,
                                 "changed": diff}})
    return specs


def from_cloud_receipt(receipt: Mapping, *, source: Mapping | None = None,
                       ttl_s: float | None = None) -> dict:
    """A `cloud_policy` proposal from a cloud tune receipt: the effort per prompt class."""
    labels = receipt.get("labels") or {}
    usd_label = labels.get("usd") or receipt.get("label") or "ESTIMATE"
    tot = receipt.get("totals") or {}
    ref = receipt.get("reference_effort", "xhigh")
    after = {c["class"]: c["chosen_effort"] for c in receipt.get("classes", [])}
    before = {c: ref for c in after}
    savings = [{"metric": "cost per request", "value": _round(tot.get("saving_pct")), "unit": "%",
                "label": usd_label,
                "baseline": tot.get("xhigh_usd_per_request"),
                "candidate": tot.get("tuned_usd_per_request"),
                "basis": f"tuned effort vs always {ref}, same prompts"}]
    en = (receipt.get("energy") or {}).get("saved_wh")
    if en:
        savings.append({"metric": "energy (hosted, modelled)", "value": _round(
            (en["low"] + en["high"]) / 2, 3), "unit": "Wh", "low": _round(en["low"], 3),
            "high": _round(en["high"], 3), "label": "ESTIMATE",
            "basis": "2 x active params x tokens FLOPs, with a joules-per-FLOP range"})
    worst_q, p99_inc, all_ok = 100.0, None, True
    for c in receipt.get("classes", []):
        if c["chosen_effort"] == ref:
            continue
        row = next((t for t in c.get("tried", [])
                    if t.get("effort") == c["chosen_effort"] and not t.get("skipped")), None)
        if row is None or not row.get("passed"):
            all_ok = False
            continue
        worst_q = min(worst_q, row["quality_pct"] - row["reference_pct"] + 100)
        lat, refl = (c.get("measured") or {}), (c.get("would_have_cost_xhigh") or {})
        if lat.get("latency_p99_s") and refl.get("latency_p99_s"):
            inc = (lat["latency_p99_s"] - refl["latency_p99_s"]) / refl["latency_p99_s"] * 100
            p99_inc = inc if p99_inc is None else max(p99_inc, inc)
    checks = {"quality": {"passed": all_ok, "gate": "fixed checks vs the xhigh answers",
                          "detail": "every changed class passed its gate"
                          if all_ok else "a class did not pass",
                          "worst_pass_rate_vs_reference_pts": _round(worst_q - 100, 1)},
              "p99": {"passed": all_ok, "gate": "p99 within budget, escape retries charged",
                      "detail": "tuned p99 vs the xhigh p99, worst class",
                      "increase_pct": _round(p99_inc, 1) if p99_inc is not None else 0.0}}
    prev = _preview("cloud", receipt.get("label") or usd_label, grade=None, receipt=receipt,
                    will_claim=["USD per request, tuned vs the xhigh default",
                                "tokens and cost from Baseten's per-response usage",
                                "energy as an ESTIMATE range"],
                    not_claimed=["not a billing statement", "no carbon claim",
                                 "energy is modelled, not metered"])
    changed = sorted(c for c in after if after[c] != ref)
    return {"kind": m.CLOUD_POLICY,
            "title": "Route effort by prompt class: "
                     + ", ".join(f"{c} {after[c]}" for c in sorted(after)),
            "summary": f"Use the cheapest reasoning effort that passed the gates for each prompt "
                       f"class instead of always {ref}. Classes changed: "
                       f"{', '.join(changed) or 'none'}. Mode: {receipt.get('mode')}.",
            "change": {"target": f"{receipt.get('provider')}/{receipt.get('model')}",
                       "before": {"effort_by_class": before},
                       "after": {"effort_by_class": after, "reference_effort": ref,
                                 "cache": "stable text first, session affinity header"
                                 if (receipt.get("cache") or {}).get("affinity_header_sent")
                                 else "unchanged"}},
            "savings": savings, "checks": checks, "receipt_preview": prev,
            "source": {"tool": "wattnot cloud tune", "receipt_id": receipt.get("receipt_id"),
                       "mode": receipt.get("mode"), **dict(source or {})},
            **({"ttl_s": ttl_s} if ttl_s else {})}


def from_moe_receipt(receipt: Mapping, *, source: Mapping | None = None,
                     ttl_s: float | None = None) -> dict:
    """A `moe_routing` proposal from an adaptive top-k receipt. Only call it when the receipt's
    gates all passed (`moe_adaptive_topk.all_gates_passed`); a failed trial is a result, not a
    proposal. The setting is OFF until a person approves and applies this."""
    blk = receipt["moe_adaptive_topk"]
    label = blk["label"]
    j, ex = blk["joules_per_token"], blk["active_experts"]
    mass, model = blk["adaptive_mass"], receipt["subject"]["model"]
    savings = [{"metric": "joules per token", "value": _round(j["saving_pct"], 2), "unit": "%",
                "label": label, "baseline": j["off"]["mean"], "candidate": j["on"]["mean"],
                "basis": f"NVML energy counter, repeats alternated, on lower in "
                         f"{j['pairs_on_lower']} pairs" if label == "MEASURED"
                         else "software model, not hardware"}]
    if ex.get("reduction_pct") is not None:
        savings.append({"metric": "experts used per token", "value": _round(ex["reduction_pct"], 1),
                        "unit": "%", "label": label, "baseline": ex["off_mean"],
                        "candidate": ex["on_mean"], "basis": "counted in the router hook"})
    g = blk["gates"]
    checks = {"quality": {"passed": g["quality"]["passed"], "gate": g["quality"]["gate"],
                          "detail": f"token agreement {g['quality']['agreement_pct']}% with "
                                    "normal top-k"},
              "p99": {"passed": g["p99"]["passed"], "gate": g["p99"]["gate"],
                      "detail": "p99 per prompt, adaptive vs normal top-k",
                      "baseline_ms": blk["p99_ms"]["off"], "candidate_ms": blk["p99_ms"]["on"],
                      "increase_pct": _round(g["p99"]["increase_pct"], 1)}}
    prev = _preview("moe_adaptive_topk", label, grade=receipt.get("grade"), receipt=receipt,
                    will_claim=["joules per token, adaptive vs normal top-k, same load",
                                "token agreement and p99 against normal top-k"],
                    not_claimed=list(blk["not_claimed"]))
    return {"kind": m.MOE_ROUTING,
            "title": f"Adaptive top-k (mass {mass:g}) on {model}",
            "summary": f"Skip the experts a token does not need: keep the top experts until "
                       f"{mass:g} of the router weight is covered. A known technique; it is "
                       f"off today. Measured against normal top-k with a transformers hook; "
                       f"label {label}. Not a vLLM kernel result.",
            "change": {"target": f"moe:{model}", "before": {"adaptive_topk": False},
                       "after": {"adaptive_topk": True, "adaptive_mass": mass, "model": model,
                                 "implementation": "transformers hook"}},
            "savings": savings, "checks": checks, "receipt_preview": prev,
            "source": {"tool": "wattnot runbook adaptive_topk",
                       "receipt_id": receipt.get("receipt_id"), **dict(source or {})},
            **({"ttl_s": ttl_s} if ttl_s else {})}


def flex_shed_spec(event: Mapping, *, estimate: Mapping, gpu: int = 0,
                   source: Mapping | None = None, ttl_s: float | None = None) -> dict:
    """A `flex_shed` proposal for one grid event. `estimate` is a dry run on the software
    power model (`delivered_pct`, `p99_increase_pct`, `quality_ok`), so it is SIMULATED."""
    tgt = float(event["target_pct"])
    return {"kind": m.FLEX_SHED,
            "title": f"Shed {tgt:g}% of GPU draw for {event['duration_s']} s ({event['id']})",
            "summary": "Lower the GPU power cap during a grid event to cut draw, while the "
                       "controller watches p99 and answer quality and backs off if either "
                       "slips. The cap goes back to its old value afterwards. GPU draw only; "
                       "cooling and the rest of the site are not shed.",
            "change": {"target": f"gpu:{gpu}", "gpu": gpu, "event": dict(event),
                       "before": {"shed_pct": 0},
                       "after": {"target_pct": tgt, "duration_s": event["duration_s"]},
                       "restores_on_exit": True},
            "savings": [{"metric": "GPU draw shed", "value": _round(estimate["delivered_pct"]),
                         "unit": "%", "label": "SIMULATED",
                         "basis": "dry run on the software power model, not hardware"}],
            "checks": {"quality": {"passed": bool(estimate.get("quality_ok")),
                                   "detail": "pass rate held in the dry run",
                                   "gate": "flex controller quality guard"},
                       "p99": {"passed": bool(estimate.get("p99_ok", True)),
                               "detail": "p99 stayed within budget in the dry run",
                               "increase_pct": estimate.get("p99_increase_pct")}},
            "receipt_preview": _preview(
                "flex", "SIMULATED", grade="SIMULATED",
                will_claim=["target vs delivered shed, time to target, ramp-back",
                            "p99 and pass rate before and during the event"],
                not_claimed=["no site or grid level saving", "no carbon claim"]),
            "source": {"tool": "wattnot flex run", **dict(source or {})},
            **({"ttl_s": ttl_s} if ttl_s else {})}
