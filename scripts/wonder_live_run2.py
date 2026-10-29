"""One LIVE Muse Wonder pass over the MEASURED RUN2 cap sweep (replayed data, live decisions).

Step 1: Wonder sees the five measured caps and picks the next trial or stops.
Step 2: Wonder writes the end-of-run efficiency summary.
No GPU is touched. Output: receipts/run2/wonder_live_run2.json
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wattnot.meta.client import GuardedClient
from wattnot.meta.ledger import SpendLedger
from wattnot.meta.wonder import WonderSupervisor

ROOT = Path(__file__).resolve().parents[1]
STEPS = ROOT / "data/h100-run2/vm/steps"
MODEL = "muse-wonder-1.0"

def trials():
    out = []
    for cap in (400, 350, 325, 450, 500):  # Spark order first, then the extra measured caps
        d = json.loads((STEPS / f"cap{cap}.json").read_text())
        q = d["quality"]
        jc = sum(d["j_per_req_cap"]) / len(d["j_per_req_cap"])
        out.append({"cap_w": cap, "clock_profile": "base",
                    "joules_per_correct": round(jc / (q["cap_pct"] / 100), 4),
                    "quality": round(q["cap_pct"] / 100, 5),
                    "p99_delta": round(d["p99_change_pct"]["mean"] / 100, 5),
                    "usd_per_1k": 0.0, "label": "MEASURED",
                    "accepted_by_gates": d["accepted"]})
    return out

class Capture:
    def __init__(self, c): self.c, self.calls = c, []
    def __call__(self, body, *, purpose="unspecified"):
        t = time.time(); raw = self.c(body, purpose=purpose)
        self.calls.append({"purpose": purpose, "response_id": raw.get("id"),
                           "model": raw.get("model"), "usage": raw.get("usage"),
                           "latency_s": round(time.time() - t, 2)})
        return raw

def main():
    ledger = SpendLedger()
    before = ledger.summary().get("total_usd")
    cap = Capture(GuardedClient(ledger=ledger))
    hist = trials()
    sup = WonderSupervisor(client=cap, model=MODEL, mock=False, threshold=0.0, patience=99,
                           candidates=[(375, "base"), (340, "base"), (300, "base")])
    d = sup.next_trial(hist)
    summ_body = {"model": MODEL, "reasoning_effort": "low", "max_tokens": 1200, "messages": [
        {"role": "system", "content": "You are Muse Wonder writing the end-of-run efficiency summary for a GPU power-cap sweep. Use only the numbers given. Gates: p99 increase <= 10%, fixed-check pass rate may drop at most 1.0 point vs the uncapped baseline. 4-6 short sentences, plain text."},
        {"role": "user", "content": json.dumps({"trials": hist, "baseline": "uncapped 700 W, pass rate 93.694%",
                                                 "next_step_decision": {"action": d.action, "cap_w": d.cap_w, "reason": d.reason}})}]}
    raw = cap(summ_body, purpose="wonder-summary:run2")
    summary = raw["choices"][0]["message"]["content"]
    after = ledger.summary().get("total_usd")
    doc = {"section": "wonder_live", "label": "LIVE", "inputs_label": "MEASURED (REPLAY of RUN2 steps; no GPU used)",
           "model": MODEL, "decision": {"action": d.action, "cap_w": d.cap_w, "clock_profile": d.clock_profile,
           "reason": d.reason, "local_trace_id": d.trace_id}, "summary": summary,
           "calls": cap.calls, "spend_usd": round((after or 0) - (before or 0), 6),
           "ledger_total_usd": after, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "trials": hist}
    p = ROOT / "receipts/run2/wonder_live_run2.json"
    p.write_text(json.dumps(doc, indent=2) + "\n")
    print(json.dumps({k: doc[k] for k in ("decision", "calls", "spend_usd", "ledger_total_usd")}, indent=1))
    print(summary)

if __name__ == "__main__":
    main()
