"""A signed receipt for one flex event (`wattnot.receipt/1`, profile `flex`).

What the receipt says, in plain terms: "the GPU was asked to cut draw by N percent; it cut
M percent within S seconds, p99 stayed under the budget, answer quality did not drop, and it
came back in R seconds". Numbers come from the run's one-second trace, which is hashed and
kept next to the receipt.

* The grade follows the measurement label: SIMULATED for the software model (always, today),
  ESTIMATE for a measured run. A flex receipt has no significance test, so it is never VALID.
* `baseline` is the window before the event. `candidate` is the settled window during it.
* The block `flex` holds the event, the controller settings, the model (if simulated), every
  cap change and the result. It is inside the hash.
* Scope: GPU draw only. Cooling, CPUs, networking and the rest of the site are not shed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wattnot.flex.run import FlexRun
from wattnot.flex.signal import Signal
from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.grade import SPEC, claimed_grade
from wattnot.receipt.joules import with_joules
from wattnot.receipt.lint import lint
from wattnot.schemas import validate_receipt

CSV_COLUMNS = ("t_s", "phase", "power_w", "cap_w", "p99_ms", "pass_rate", "throughput_rps",
               "target_w", "sla_ms")
WINDOW = 10  # ticks per energy sample
SCOPE = "GPU draw only. Cooling, CPUs, networking and the rest of the site are not shed."
NOT_CLAIMED = [
    "no site or grid level saving",
    "no carbon claim",
    "not a utility program enrolment or a settled payment",
]


def trace_csv(run: FlexRun) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for k in run.ticks:
        w.writerow([f"{k.t_s:g}", k.phase, f"{k.power_w:.3f}", k.cap_w, f"{k.p99_ms:.2f}",
                    f"{k.pass_rate:.4f}", f"{k.throughput_rps:.3f}",
                    "" if k.target_w is None else f"{k.target_w:.2f}",
                    "" if k.sla_ms is None else f"{k.sla_ms:.2f}"])
    return buf.getvalue()


def trace_json(run: FlexRun, label: str) -> dict:
    """The same trace as JSON, for charts."""

    return {"label": label, "dt_s": run.cfg.dt_s, "event": run.event.to_dict(),
            "points": [{"t_s": k.t_s, "phase": k.phase, "power_w": round(k.power_w, 3),
                        "cap_w": k.cap_w, "p99_ms": round(k.p99_ms, 2),
                        "pass_rate": round(k.pass_rate, 4),
                        "throughput_rps": round(k.throughput_rps, 3),
                        "target_w": None if k.target_w is None else round(k.target_w, 2),
                        "sla_ms": None if k.sla_ms is None else round(k.sla_ms, 2)}
                       for k in run.ticks]}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _arm(ticks, run: FlexRun, window_ticks) -> dict:
    """Energy per request from windows of `WINDOW` ticks: mean power over mean throughput."""

    wh = []
    size = min(WINDOW, len(window_ticks))
    for i in range(0, len(window_ticks) - size + 1, size):
        w = window_ticks[i:i + size]
        rps = statistics.fmean(k.throughput_rps for k in w)
        if rps > 0:
            wh.append(statistics.fmean(k.power_w for k in w) / rps / 3600)
    if not wh:
        raise ValueError("not enough ticks in a window to measure energy per request")
    return {
        "params": {"power_cap_w": window_ticks[-1].cap_w}, "reps": len(wh),
        "wh_per_req": {"mean": round(statistics.fmean(wh), 9),
                       "sd": round(statistics.stdev(wh), 9) if len(wh) > 1 else 0.0,
                       "samples": [round(x, 9) for x in wh]},
        "p99_ms": round(max(k.p99_ms for k in window_ticks), 2),
        "quality": {"fixed_pass_rate": round(statistics.fmean(
            k.pass_rate for k in window_ticks), 4)},
    }


def build_flex_receipt(run: FlexRun, signal: Signal, *, trace_name: str | None = None,
                       created_at: str | None = None,
                       prev_sha256: str | None = None,
                       approval: dict | None = None) -> tuple[dict[str, Any], str, dict]:
    """Return (receipt, trace_csv_text, trace_json). Raises ValueError if it fails the checks."""

    label = (run.meter or {}).get("label", "SIMULATED")
    if label not in ("SIMULATED", "MEASURED"):
        raise ValueError("backend label must be SIMULATED or MEASURED")
    csv_text = trace_csv(run)
    csv_sha = _sha(csv_text.encode())
    tj = trace_json(run, label)
    tj_sha = _sha(json.dumps(tj, sort_keys=True, separators=(",", ":")).encode())
    rid = f"rcpt_flex_{csv_sha[:12]}"
    trace_name = trace_name or f"{rid}.trace.csv"
    res = run.result
    before = [k for k in run.ticks if k.phase == "before"][len(
        [k for k in run.ticks if k.phase == "before"]) // 3:]
    settled = [k for k in run.ticks if k.phase == "event" and k.t_s >= res["settled_from_s"]]
    base, cand = _arm(run.ticks, run, before), _arm(run.ticks, run, settled)
    b, c = base["wh_per_req"]["mean"], cand["wh_per_req"]["mean"]
    moves = [[round(t, 1), w] for t, w in run.moves]
    r: dict[str, Any] = {
        "spec": SPEC, "profile": "flex", "receipt_id": rid,
        "created_at": created_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": f"flex_{run.event.id}",
        "subject": {"model": "demo model (not named)",
                    "host": {"provider": "software power model" if label == "SIMULATED"
                             else "own GPU", "gpu": "H100 (modelled)" if label == "SIMULATED"
                             else "see run notes", "driver": "n/a"},
                    "mode": "flex", "workload": {"name": "steady demo load",
                                                 "requests_per_s": round(statistics.fmean(
                                                     k.throughput_rps for k in before), 3)}},
        "baseline": base, "candidate": cand,
        "result": {
            "saving_pct": {"mean": round(100 * (b - c) / b, 4), "ci_low": None, "ci_high": None,
                           "basis": "energy per request, settled event window vs before"},
            "welch": {"applicable": False,
                      "reason": "one event, no repeated runs to test; grade capped"},
            "billable": False, "billed_saving_pct": 0.0, "fee_share": 0.0, "currency": "USD"},
        "measurement": {"meter": (run.meter or {}).get("source", "unknown"),
                        "hz": 1.0 / run.cfg.dt_s,
                        "csv": [{"path": trace_name, "sha256": csv_sha}], "label": label},
        "flex": {
            "label": label,
            "event": run.event.to_dict(),
            "signal": {"label": signal.label, "source": signal.source},
            "outcome": {k: res[k] for k in (
                "status", "limit", "target_pct", "target_w", "delivered_pct", "delivered_w",
                "seconds_to_target", "ramp_back_s", "settled_cap_w", "p99_ms", "pass_rate")},
            "baseline_power_w": round(run.baseline["power_w"], 2),
            "controller": run.cfg.to_dict(),
            "model": (run.meter or {}).get("model"),
            "cap_changes": moves[:200],
            "cap_change_count": len(moves),
            "trace": {"csv_sha256": csv_sha, "json_sha256": tj_sha, "points": len(run.ticks)},
            "scope": SCOPE, "not_claimed": NOT_CLAIMED,
        },
        "planner_trace": [],
    }
    if approval:  # points back at the approved proposal (id and content hash)
        r["approval"] = dict(approval)
    with_joules(r)
    r["grade"] = claimed_grade(r, rekor=False)
    r["integrity"] = {"canonicalization": "JCS RFC 8785", "sha256": canonical_sha256(r),
                      "prev_sha256": prev_sha256, "signer": None, "rekor": None}
    problems = lint(r)
    if label == "SIMULATED" and "MEASURED" in json.dumps(r["flex"]):
        problems.append("a SIMULATED flex receipt must not say MEASURED")
    from wattnot.cloud.receipt import BANNED

    low = json.dumps(r).lower()
    problems += [f"banned wording: {w}" for w in BANNED if w in low]
    if problems:
        raise ValueError("flex receipt failed checks: " + "; ".join(problems))
    validate_receipt(r)
    return r, csv_text, tj


def write_flex_receipt(run: FlexRun, signal: Signal, out: str | Path, *, created_at=None,
                       prev_sha256=None, sign_key: str | None = None,
                       approval: dict | None = None) -> dict:
    """Write `<id>.json`, `<id>.trace.csv`, `<id>.trace.json` (and a bundle if `sign_key`)."""

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    r, csv_text, tj = build_flex_receipt(run, signal, created_at=created_at,
                                         prev_sha256=prev_sha256, approval=approval)
    rid = r["receipt_id"]
    (out / f"{rid}.trace.csv").write_text(csv_text, encoding="utf-8")
    (out / f"{rid}.trace.json").write_text(json.dumps(tj, separators=(",", ":")) + "\n",
                                           encoding="utf-8")
    path = out / f"{rid}.json"
    path.write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    if sign_key:
        from wattnot.receipt.sign import sign
        sign(path, no_rekor=True, key_path=sign_key)
    return {"receipt": r, "path": str(path)}


def check_trace(receipt_path: str | Path) -> tuple[bool, list[str]]:
    """Re-hash the trace files next to a flex receipt. Returns (ok, problems)."""

    p = Path(receipt_path)
    r = json.loads(p.read_text(encoding="utf-8"))
    problems: list[str] = []
    fx = r.get("flex")
    if not isinstance(fx, dict):
        return False, ["not a flex receipt (no flex block)"]
    try:
        entry = r["measurement"]["csv"][0]
        data = (p.parent / entry["path"]).read_bytes()
    except (OSError, KeyError, IndexError):
        return False, ["trace csv not found next to the receipt"]
    if _sha(data) != entry["sha256"] or entry["sha256"] != fx["trace"]["csv_sha256"]:
        problems.append("trace csv does not match its recorded sha256")
    rows = list(csv.DictReader(io.StringIO(data.decode())))
    if len(rows) != fx["trace"]["points"]:
        problems.append(f"trace has {len(rows)} rows, receipt says {fx['trace']['points']}")
    tj = p.with_name(p.name.removesuffix(".json") + ".trace.json")
    if tj.exists():
        doc = json.loads(tj.read_text(encoding="utf-8"))
        want = _sha(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode())
        if want != fx["trace"]["json_sha256"]:
            problems.append("trace json does not match its recorded sha256")
    return not problems, problems
