"""Muse Spark as the agent that runs the flow: four live calls, each one a traced step.

    probe     Spark reads the live GPU telemetry and the preflight, says what it can change
    proposal  Spark proposes the power cap (or a serving change) from the measured sweep
    gate      Spark judges the quality gate; its verdict sits next to the fixed rule's verdict
    receipt   Spark explains the signed receipt (energy, SCI carbon, water) in plain words

Every call goes through the guarded Meta client (spend cap, ledger). Each step records a trace
id (the API response id, else a local id), latency (MEASURED, wall clock), tokens (MEASURED,
from the API usage) and $ cost (ESTIMATE: usage x the published price). Steps are written as
`wattnot.event/1` step.started / step.finished events with `data.kind = "spark_call"`, so the
dashboard trace panel and the architecture map pick them up from the same event log.

Spark never overrides the measured gate: the receipt keeps the fixed rule's verdict, and the
trace says whether Spark agreed. WATTNOT_META_MOCK=1 swaps the network for a canned answer and
labels every step MOCK.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path

from wattnot.meta.client import GuardedClient, meta_mock_on

MODEL = os.environ.get("WATTNOT_SPARK_MODEL", "muse-spark-1.3")
STEPS = ("probe", "proposal", "gate", "receipt")
SYSTEM = ("You are WattNot's operator agent. You manage GPU power caps for an LLM server. "
          "Answer only with one JSON object, no prose outside it. Never claim carbon neutrality "
          "or offsets. Use only the numbers given.")
ASK = {
    "probe": "Read this GPU telemetry and preflight. Return JSON {\"gpu\": str, \"power_w\": "
             "number|null, \"can_cap\": bool, \"blockers\": [str], \"next\": str}.",
    "proposal": "From this measured sweep, propose one change. Return JSON {\"action\": "
                "\"set_power_cap\"|\"serving_change\"|\"none\", \"cap_w\": number|null, "
                "\"why\": str, \"expected_saving_pct\": number|null}.",
    "gate": "Judge the quality gate on these numbers: the cap passes only if quality at the cap "
            "is within the allowed drop of stock and p99 stays inside the SLA. Return JSON "
            "{\"verdict\": \"PASS\"|\"FAIL\", \"why\": str}.",
    "receipt": "Explain this signed receipt to an operator in at most 3 sentences: energy per "
               "correct answer, the SCI carbon and water numbers with their labels. Return JSON "
               "{\"summary\": str, \"labels_ok\": bool}.",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _mock_opener(req, timeout):
    body = json.loads(req.data)
    step = body.get("metadata", {}).get("step", "probe")
    canned = {"probe": {"gpu": "mock", "power_w": None, "can_cap": False, "blockers": ["mock"],
                        "next": "run the sweep"},
              "proposal": {"action": "set_power_cap", "cap_w": 325, "why": "mock",
                           "expected_saving_pct": None},
              "gate": {"verdict": "PASS", "why": "mock"},
              "receipt": {"summary": "mock", "labels_ok": True}}[step]
    out = {"id": "mock-" + secrets.token_hex(6), "choices": [{"message": {
        "content": json.dumps(canned)}}], "usage": {"prompt_tokens": 200, "completion_tokens": 40}}
    return 200, json.dumps(out).encode()


def _parse(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").split("\n", 1)[-1]
    i, j = t.find("{"), t.rfind("}")
    try:
        return json.loads(t[i:j + 1]) if i >= 0 else {}
    except ValueError:
        return {}


class Events:
    """Appends wattnot.event/1 lines; the dashboard follows this file."""

    def __init__(self, path: Path, run_id: str) -> None:
        self.path, self.run_id = path, run_id
        path.parent.mkdir(parents=True, exist_ok=True)
        self.seq = sum(1 for _ in path.open()) if path.exists() else 0

    def emit(self, type_: str, step_id: str, data: dict) -> None:
        ev = {"schema_version": "wattnot.event/1", "run_id": self.run_id, "seq": self.seq,
              "ts": _now(), "type": type_, "step_id": step_id, "parent_step_id": "spark",
              "data": data}
        with self.path.open("a") as f:
            f.write(json.dumps(ev) + "\n")
        self.seq += 1


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S_") + secrets.token_hex(3)


def run_trace(inputs: Mapping[str, Mapping], *, events: Path, run_id: str | None = None,
              client: Callable | None = None, fixed_gate: str | None = None) -> dict:
    """Four Spark calls. `inputs` maps each step name to the facts it is shown."""

    mock = meta_mock_on()
    label = "MOCK" if mock else "MEASURED"
    if client is None and mock:   # canned answers, kept out of the real spend ledger
        import tempfile
        from wattnot.meta.ledger import SpendLedger
        led = SpendLedger(Path(tempfile.mkdtemp(prefix="spark-mock-")) / "ledger.json")
        client = GuardedClient(opener=_mock_opener, key="mock", ledger=led)
    client = client or GuardedClient()
    ev = Events(events, run_id or new_run_id())
    calls = []
    for step in STEPS:
        local = "sp-" + secrets.token_hex(6)
        sid = f"spark.{step}"
        ev.emit("step.started", sid, {"kind": "spark_call", "step": step, "node": "spark",
                                      "sub": step, "label": label, "model": MODEL,
                                      "summary": f"Muse Spark: {step}"})
        body = {"model": MODEL, "max_completion_tokens": 400,
                "messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": ASK[step] + "\n\n" +
                              json.dumps(inputs.get(step, {}), default=str)[:6000]}],
                "metadata": {"step": step}}
        t0 = time.monotonic()
        rec = {"step": step, "model": MODEL, "label": label, "status": "ok"}
        try:
            data = client(body, purpose=f"spark-trace:{step}:{local}")
            rec["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
            rec["trace_id"] = data.get("id") or local
            msg = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            rec["answer"] = _parse(msg)
            u = data.get("usage") or {}
            rec["tokens"] = {"in": u.get("prompt_tokens"), "out": u.get("completion_tokens"),
                             "label": label}
            rec["usd"] = _usd_from_ledger(client, f"spark-trace:{step}:{local}")
        except Exception as e:  # noqa: BLE001 - a failed call is shown, never faked
            rec.update(status="failed", trace_id=local, error=f"{type(e).__name__}: {e}"[:200],
                       latency_ms=round((time.monotonic() - t0) * 1000, 1), usd=None)
        rec["latency_label"] = label
        rec["usd_label"] = "MOCK" if mock else "ESTIMATE"   # usage x published price
        if step == "gate":
            rec["fixed_gate"] = fixed_gate
            v = (rec.get("answer") or {}).get("verdict")
            rec["agrees_with_fixed_gate"] = None if fixed_gate is None or v is None \
                else v == fixed_gate
        calls.append(rec)
        ev.emit("step.finished", sid, {"kind": "spark_call", "node": "spark", "sub": step,
                                       "status": rec["status"], "duration_s":
                                       rec["latency_ms"] / 1000, **{k: v for k, v in rec.items()
                                                                    if k != "status"},
                                       "summary": _summary(rec)})
    total = sum(c["usd"] or 0 for c in calls)
    return {"run_id": ev.run_id, "model": MODEL, "label": label, "calls": calls,
            "ok": all(c["status"] == "ok" for c in calls),
            "total_usd": round(total, 6), "total_usd_label": calls[0]["usd_label"],
            "events": str(events)}


def _usd_from_ledger(client, purpose: str) -> float | None:
    led = getattr(client, "ledger", None)
    if led is None:
        return None
    e = next((x for x in reversed(led.entries()) if x.get("purpose") == purpose), None)
    return None if e is None else e.get("usd")


def _summary(rec: Mapping) -> str:
    a = rec.get("answer") or {}
    s = {"probe": lambda: f"GPU {a.get('gpu')}, can cap: {a.get('can_cap')}",
         "proposal": lambda: f"{a.get('action')} {a.get('cap_w') or ''}".strip(),
         "gate": lambda: f"Spark {a.get('verdict')}, fixed gate {rec.get('fixed_gate')}",
         "receipt": lambda: str(a.get("summary", ""))[:120]}[rec["step"]]
    return s() if rec["status"] == "ok" else rec.get("error", "failed")


# ---- inputs from a flow output folder ------------------------------------------------------

def _load(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def inputs_from_flow(out: Path, receipt: Path | None) -> tuple[dict, str | None]:
    pre = (out / "logs" / "preflight.log")
    probe = _load(out / "probe" / "caps.json")
    r = _load(receipt) if receipt else None
    r = r or {}
    sweep, gate_in, fixed = {}, {}, None
    if "steps" in r:   # proof receipt
        sweep = {"best_cap_w": r.get("best_cap_w"),
                 "steps": [{k: s.get(k) for k in ("cap_w", "label", "quality", "energy_change_pct",
                                                  "p99_change_pct", "accepted", "why_not")}
                           for s in r["steps"]]}
        gate_in = sweep
        best = next((s for s in r["steps"] if s.get("cap_w") == r.get("best_cap_w")), None)
        fixed = "PASS" if best is not None and best.get("accepted") else "FAIL"
    elif "candidate" in r:
        sweep = {k: {"cap_w": r[k]["params"].get("power_cap_w"),
                     "wh_per_req": r[k]["wh_per_req"]["mean"],
                     "pass_rate": (r[k].get("quality") or {}).get("fixed_pass_rate"),
                     "p99_ms": r[k].get("p99_ms")} for k in ("baseline", "candidate")}
        gate_in = {**sweep, "gates": (r.get("result") or {}).get("gates")}
        res = r.get("result") or {}
        q = [(r[k].get("quality") or {}).get("fixed_pass_rate") for k in ("baseline", "candidate")]
        held = None not in q and q[1] >= q[0] - 0.01
        fixed = "PASS" if res.get("billable") and held else "FAIL"
    rcpt = {k: r.get(k) for k in ("receipt_id", "grade", "joules", "sci", "result")} if r else {}
    return ({"probe": {"preflight": pre.read_text()[-3000:] if pre.exists() else None,
                       "nvml": probe},
             "proposal": sweep, "gate": gate_in, "receipt": rcpt}, fixed)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--flow-out", required=True)
    ap.add_argument("--receipt")
    ap.add_argument("--events", help="event log to append to (default FLOW_OUT/spark/events.jsonl)")
    ap.add_argument("--run-id")
    a = ap.parse_args(argv)
    out = Path(a.flow_out)
    inputs, fixed = inputs_from_flow(out, Path(a.receipt) if a.receipt else None)
    ev = Path(a.events) if a.events else out / "spark" / "events.jsonl"
    res = run_trace(inputs, events=ev, run_id=a.run_id, fixed_gate=fixed)
    (out / "spark").mkdir(parents=True, exist_ok=True)
    (out / "spark" / "trace.json").write_text(json.dumps(res, indent=2))
    for c in res["calls"]:
        print(f"[spark] {c['step']:9} {c['status']:6} {c['trace_id']} "
              f"{c['latency_ms']:.0f} ms [{c['latency_label']}] "
              f"${c['usd'] if c['usd'] is not None else '-'} [{c['usd_label']}]")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
