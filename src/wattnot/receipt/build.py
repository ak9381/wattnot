"""Build a receipt from a finished run.

Input is a run summary (`runs/<id>/run.json`) with per-rep Wh samples for
the baseline and the candidate, plus the run's `events.jsonl` for the
planner trace. Money is US dollars from an assumed tariff. Water is always
an ESTIMATE from an assumed WUE. The noise gate (Welch + bootstrap CI)
decides what is billable. The hash is JCS + SHA-256 over everything except
`integrity`, chained to the previous receipt with `prev_sha256`.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wattnot.gates.noise import noise_gate
from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.grade import SPEC, claimed_grade
from wattnot.receipt.joules import with_joules
from wattnot.receipt.lint import lint
from wattnot.receipt.per_model import attach as attach_per_model
from wattnot.schemas import validate_receipt

TARIFF_USD_PER_KWH = 0.12  # assumption, shown as such
WUE_L_PER_KWH = 1.8  # assumption, so water is an ESTIMATE
FEE_SHARE = 0.20
WATER_METHOD = "Wh/request x WUE (L/kWh), site energy only"
WATER_SOURCE = "assumed WUE 1.8 L/kWh (industry average); not measured"


def _stats(samples: Sequence[float], nd: int = 6) -> dict:
    return {"mean": round(statistics.fmean(samples), nd),
            "sd": round(statistics.stdev(samples), nd) if len(samples) > 1 else 0.0,
            "samples": [round(s, nd) for s in samples]}


def _arm(arm: Mapping, tariff: float) -> dict:
    wh = list(arm.get("wh_samples") or (arm.get("wh_per_req") or {}).get("samples") or [])
    usd = [w / 1000 * tariff for w in wh]
    out = {"params": dict(arm["params"]), "reps": len(wh), "wh_per_req": _stats(wh),
           "usd_per_req": _stats(usd, 10),
           "p99_ms": arm["p99_ms"], "quality": dict(arm["quality"])}
    return out


def planner_trace_from_events(events: Sequence[Mapping]) -> list[dict]:
    trace, n = [], 0
    for e in events:
        if e.get("type") != "decision":
            continue
        n += 1
        d = e["data"]
        c = d.get("choice", {})
        params = ({"power_cap_w": c.get("power_cap_w")} if c.get("kind") == "cap"
                  else {c.get("knob"): c.get("value")} if c.get("kind") == "knob" else {})
        fell_back = (d.get("model") in ("fallback", "bisect-fallback")
                     or bool((d.get("call") or {}).get("fallback")))
        row = {"trial_no": n, "source": "fallback" if fell_back else "spark",
               "model": d.get("model"), "kind": c.get("kind"),
               "params": params, "reason": d.get("reason", ""),
               "tokens": d.get("tokens", {"input": 0, "output": 0})}
        if d.get("call"):  # provider, model id, prompt hash, latency of the planner call
            row["call"] = dict(d["call"])
        trace.append(row)
    return trace


def build_receipt(run: Mapping, *, events: Sequence[Mapping] = (), prev_sha256: str | None = None,
                  rekor: bool = False, created_at: str | None = None,
                  tariff_usd_per_kwh: float = TARIFF_USD_PER_KWH,
                  wue_l_per_kwh: float = WUE_L_PER_KWH, n_boot: int = 10_000,
                  seed: int = 7, judge_verdict: str | None = None,
                  judge_score: float | None = None, judge_model: str | None = None,
                  wonder_trace_ids: Sequence[str] | str | None = None,
                  per_model_spend: Mapping[str, Any] | None = None,
                  musecode_patch_sha256: str | None = None,
                  musecode_pr_sha256: str | None = None,
                  musecode_pr_url: str | None = None,
                  hotspot_cap: Mapping[str, Any] | None = None) -> dict[str, Any]:
    base, cand = run["baseline"], run["candidate"]
    base_samples = base.get("wh_samples") or (base.get("wh_per_req") or {}).get("samples") or []
    cand_samples = cand.get("wh_samples") or (cand.get("wh_per_req") or {}).get("samples") or []
    gate = noise_gate(base_samples, cand_samples, n_boot=n_boot, seed=seed)
    d = gate.detail
    base_arm, cand_arm = _arm(base, tariff_usd_per_kwh), _arm(cand, tariff_usd_per_kwh)
    receipt: dict[str, Any] = {
        "spec": SPEC,
        "receipt_id": run.get("receipt_id") or f"rcpt_{run['run_id'][4:]}",
        "created_at": created_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "run_id": run["run_id"],
        "subject": dict(run["subject"]),
        "baseline": base_arm,
        "candidate": cand_arm,
        "result": {"saving_pct": {"mean": d["saving_pct"], "ci_low": d["ci_low"],
                                  "ci_high": d["ci_high"]},
                   "welch": {"t": d["t"], "df": d["df"], "p_value": d["p_value"],
                             "alpha": d["alpha"]},
                   "bootstrap": {"n": n_boot, "seed": seed, "level": 0.95},
                   "billable": gate.passed, "billed_saving_pct": d["billed_saving_pct"],
                   "fee_share": FEE_SHARE, "currency": "USD",
                   "tariff_usd_per_kwh": {"value": tariff_usd_per_kwh, "label": "ESTIMATE"}},
        "measurement": dict(run["measurement"]),
        "attribution": dict(run.get("attribution") or {
            "method": "token-share-capped (after Neuralwatt)", "cap": 1.0}),
        "planner_trace": list(run.get("planner_trace") or planner_trace_from_events(events)),
        "estimates": {
            "water_ml_per_req": {"value": round(cand_arm["wh_per_req"]["mean"] * wue_l_per_kwh, 6),
                                 "label": "ESTIMATE", "method": WATER_METHOD,
                                 "source": WATER_SOURCE},
            "baseline_water_ml_per_req": {
                "value": round(base_arm["wh_per_req"]["mean"] * wue_l_per_kwh, 6),
                "label": "ESTIMATE", "method": WATER_METHOD, "source": WATER_SOURCE}},
    }
    if run.get("compare_to"):
        receipt["compare_to"] = run["compare_to"]
    attach_per_model(receipt, events=events, roles=run.get("models") or ())

    # Attach judge fields, wonder trace IDs, per-model spend, and musecode PR/patch hash
    m = receipt["measurement"]
    j_verdict = judge_verdict or run.get("judge_verdict")
    j_score = judge_score if judge_score is not None else run.get("judge_score")
    j_model = judge_model or run.get("judge_model")

    if isinstance(run.get("judge"), Mapping):
        j_verdict = j_verdict or run["judge"].get("verdict")
        if j_score is None:
            j_score = run["judge"].get("score")
            if j_score is None:
                j_score = run["judge"].get("agreement")
        j_model = j_model or run["judge"].get("model")

    if j_verdict is None or j_score is None:
        for ev in events:
            if ev.get("type") == "gate.result" and (ev.get("data") or {}).get("check") == "judge":
                ed = ev["data"]
                if j_verdict is None:
                    j_verdict = "pass" if ed.get("passed") else "fail"
                if j_score is None:
                    j_score = ed.get("agreement")
                    if j_score is None:
                        j_score = ed.get("score")
                    if j_score is None and "worse_rate" in ed:
                        j_score = round(1.0 - float(ed.get("worse_rate", 0.0)), 4)
                if j_model is None:
                    j_model = ed.get("model")
                break

    if j_verdict is not None:
        m["judge_verdict"] = str(j_verdict)
    if j_score is not None:
        m["judge_score"] = round(float(j_score), 4)
    if j_model is not None:
        m["judge_model"] = str(j_model)
    elif j_verdict is not None:
        m["judge_model"] = "meta-llama/Llama-3.1-8B-Instruct"

    w_tids = wonder_trace_ids or run.get("wonder_trace_ids") or run.get("wonder_trace_id")
    if w_tids is not None:
        m["wonder_trace_ids"] = [w_tids] if isinstance(w_tids, str) else list(w_tids)

    pm_spend = per_model_spend or run.get("per_model_spend")
    if pm_spend is not None:
        m["per_model_spend"] = dict(pm_spend)

    mc_patch = (musecode_patch_sha256 or run.get("musecode_patch_sha256")
                or run.get("musecode_patch_hash"))
    if mc_patch is not None:
        m["musecode_patch_sha256"] = str(mc_patch)

    mc_pr = musecode_pr_sha256 or run.get("musecode_pr_sha256") or run.get("musecode_pr_hash")
    if mc_pr is not None:
        m["musecode_pr_sha256"] = str(mc_pr)

    mc_url = musecode_pr_url or run.get("musecode_pr_url")
    if mc_url is not None:
        m["musecode_pr_url"] = str(mc_url)

    hc = hotspot_cap or run.get("hotspot_cap")
    if hc is None:
        for ev in events:
            if ev.get("type") in ("sam.hotspot_cap", "hotspot_cap"):
                hc = ev.get("data")
                break
    if hc is not None:
        hc_mask = hc.get("mask") or {}
        hc_decision = hc.get("decision") or {}
        hc_reason = str(hc.get("reason", ""))
        mask_sha = hc_mask.get("sha256")
        if not mask_sha:
            canonical = json.dumps(
                {
                    "bbox": hc_mask.get("bbox"),
                    "polygon": hc_mask.get("polygon"),
                    "rle": hc_mask.get("rle"),
                },
                sort_keys=True,
            )
            mask_sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        hc_entry = {
            "mask": {
                "bbox": list(hc_mask.get("bbox") or []),
                "polygon": list(hc_mask.get("polygon") or []),
                "rle": hc_mask.get("rle"),
                "sha256": str(mask_sha),
                "label": str(hc_mask.get("label", "MOCK")),
            },
            "decision": {
                "gpu_id": hc_decision.get("gpu_id"),
                "old_cap_w": hc_decision.get("old_cap_w"),
                "new_cap_w": hc_decision.get("new_cap_w"),
                "delta_w": hc_decision.get("delta_w", (
                    (hc_decision.get("new_cap_w") - hc_decision.get("old_cap_w"))
                    if hc_decision.get("new_cap_w") is not None and hc_decision.get("old_cap_w") is not None
                    else None
                )),
                "min_cap_w": hc_decision.get("min_cap_w", 250),
                "label": str(hc_decision.get("label", "ADVICE")),
            },
            "reason": hc_reason[:195],
        }
        m["hotspot_cap"] = hc_entry
        receipt["hotspot_cap"] = hc_entry

    with_joules(receipt)
    receipt["grade"] = claimed_grade(receipt, rekor=rekor)
    receipt["integrity"] = {"canonicalization": "JCS RFC 8785",
                            "sha256": canonical_sha256(receipt), "prev_sha256": prev_sha256,
                            "signer": None, "rekor": None}
    problems = lint(receipt)
    if problems:
        raise ValueError("receipt failed lint: " + "; ".join(problems))
    validate_receipt(receipt)
    return receipt


def build_from_dir(run_dir: str | Path, out_dir: str | Path, **kw) -> Path:
    run_dir = Path(run_dir)
    run = json.loads((run_dir / "run.json").read_text())
    ev_path = run_dir / "events.jsonl"
    events = [json.loads(x) for x in ev_path.read_text().splitlines() if x.strip()] \
        if ev_path.exists() else []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prev = sorted(p for p in out_dir.glob("rcpt_*.json")
                  if not p.name.endswith(".sigstore.json"))  # skip signature bundles
    if "prev_sha256" not in kw and prev:
        kw["prev_sha256"] = json.loads(prev[-1].read_text())["integrity"]["sha256"]
    r = build_receipt(run, events=events, **kw)
    path = out_dir / f"{r['receipt_id']}.json"
    path.write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    return path


def verify_hash(receipt: Mapping) -> tuple[bool, str, str]:
    """(ok, expected, got) for the receipt's stored sha256."""

    want = (receipt.get("integrity") or {}).get("sha256", "")
    got = canonical_sha256(receipt)
    return want == got, want, got
