"""Build a receipt from a finished run.

Input is a run summary (`runs/<id>/run.json`) with per-rep Wh samples for
the baseline and the candidate, plus the run's `events.jsonl` for the
planner trace. Money is US dollars from an assumed tariff. Water is always
an ESTIMATE from an assumed WUE. The noise gate (Welch + bootstrap CI)
decides what is billable. The hash is JCS + SHA-256 over everything except
`integrity`, chained to the previous receipt with `prev_sha256`.
"""

from __future__ import annotations

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
    wh = list(arm["wh_samples"])
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
                  seed: int = 7) -> dict[str, Any]:
    base, cand = run["baseline"], run["candidate"]
    gate = noise_gate(base["wh_samples"], cand["wh_samples"], n_boot=n_boot, seed=seed)
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
