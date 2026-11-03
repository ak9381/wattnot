"""Write the gateway's live logs in the harness run format, so the existing 8771 views
(live graph, run picker, receipts, EU report, Ask) read a gateway run like any other round.

Output: <out_dir>/round.json (results.per_arm like the H100/Modal rounds) and
<out_dir>/events.jsonl (run.started / step.finished / run.finished, the launch event shape).
Rewritten in full on each call; safe to call on a timer while traffic runs.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from wattnot.gateway.plug import _read


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def export(savings_log, out_dir, *, round_id: str = "gw", model: str | None = None,
           provider: str = "own_gpu", status: str = "running") -> dict:
    log = Path(savings_log)
    recs = _read(log)
    wo = _read(log.with_name(log.stem + ".without.jsonl"))
    shadow = _read(log.with_name(log.stem + ".shadow.jsonl"))
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    jl = "MEASURED" if recs and all(r["labels"]["joules"].startswith("MEASURED") for r in recs) else "ESTIMATE"
    agree = (sum(p["agree"] for p in shadow) / len(shadow)) if shadow else None

    def arm(rows, j, usd):
        jj = sum(j(x) for x in rows)
        return {"answers": len(rows), "energy_j": {"value": round(jj, 1), "label": jl},
                "usd": {"value": round(sum(usd(x) for x in rows), 6), "label": "ESTIMATE"}}
    w = arm(recs, lambda r: r["actual"]["joules"], lambda r: r["actual"]["usd"])
    if wo:   # Test on my prompts: a real side-by-side stock arm
        b = arm(wo, lambda x: x.get("joules", 0), lambda x: x.get("usd", 0))
    else:    # Go live: WattNot only; stock is the per-request baseline from the Test step / harness
        wo = [{"ts": r["ts"], **r["baseline"]} for r in recs]
        b = arm(wo, lambda x: x.get("joules", 0), lambda x: x.get("usd", 0))
        b["energy_j"]["label"] = "ESTIMATE (stock baseline from Test step)"
        b["baseline_only"] = True
    seq = log.with_name("sequential.json")
    if seq.exists():   # one GPU, arms one after the other: MEASURED NVML joules per arm
        d = json.loads(seq.read_text())
        for a, dst in (("with", w), ("without", b)):
            if d.get(a):
                dst["energy_j"] = {"value": d[a]["joules"], "label": "MEASURED"}
                dst["correct"], dst["cap_w"] = d[a].get("correct"), d[a].get("cap_w")
        jl = "MEASURED"
    res = {"per_arm": {"with_wattnot": w, "without_wattnot": b}}
    if recs and wo:
        wj, bj = w["energy_j"]["value"] / len(recs), b["energy_j"]["value"] / len(wo)
        wu, bu = w["usd"]["value"] / len(recs), b["usd"]["value"] / len(wo)
        res["energy_saved_pct"] = round(100 * (bj - wj) / bj, 2) if bj else None
        res["usd_saved_pct"] = round(100 * (bu - wu) / bu, 2) if bu else None
    if agree is not None and recs:   # ESTIMATE: correct = shadow agreement with stock
        res["j_per_correct_with"] = round(w["energy_j"]["value"] / max(agree * len(recs), 1e-9), 1)
    started = min([r["ts"] for r in recs + wo] or [time.time()])
    rnd = {"round": round_id, "status": status, "model": model or (recs[0]["baseline"]["model"] if recs else None),
           "label": jl, "kind": "gateway", "started_utc": _iso(started), "ended_utc": _iso(time.time()),
           "results": res, "spark_summary": {"agreement": agree}, "provider": provider,
           "spend": {"usd": w["usd"]["value"] + b["usd"]["value"], "label": "ESTIMATE"}}
    (out / "round.json").write_text(json.dumps(rnd, indent=1))
    ev, n = [], 0

    def e(t, ts, a, data):
        nonlocal n; n += 1
        ev.append({"type": t, "ts": _iso(ts), "run": f"{round_id}-{a}", "step_id": f"e{n}",
                   "data": {"arm": a, **data}, "arm": a})
    for a, rows in (("without_wattnot", wo), ("with_wattnot", recs)):
        if not rows:
            continue
        e("run.started", rows[0]["ts"], a, {"run": f"{round_id} {a}", "model": rnd["model"], "mode": "live", "label": jl})
        for x in rows:
            act = x.get("actual", x)
            e("step.finished", x["ts"], a, {
                "node": "gateway", "sub": "call", "model": (act.get("models") or [x.get("model")])[0],
                "status": "ok" if x.get("status", 200) == 200 else "error",
                "usd": act.get("usd"), "usd_label": "ESTIMATE", "label": jl, "joules": act.get("joules"),
                "completion_tokens": act.get("completion_tokens"), "receipt_id": x.get("receipt_id"),
                "summary": f"{a}: {act.get('completion_tokens')} tokens"})
        if status != "running":
            e("run.finished", rows[-1]["ts"], a, {})
    (out / "events.jsonl").write_text("".join(json.dumps(v) + "\n" for v in ev))
    return rnd


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--round", default="gw"); ap.add_argument("--every", type=float, default=0)
    ap.add_argument("--status", default="running")
    a = ap.parse_args()
    while True:
        export(a.log, a.out, round_id=a.round, status=a.status)
        if not a.every:
            break
        time.sleep(a.every)
