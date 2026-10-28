"""Hosted A/B: the same prompts on Baseten and Modal (Muse Spark Open), once without
WattNot and once with the WattNot levers that work through an API.

Levers on a shared per-token endpoint (no GPU access):
* routing    - with: try effort "low" first and step up to "medium" only when the fixed
               answer check fails; without: every prompt at "medium".
* gate       - the quality gate: if the with-arm passes fewer answers than the
               without-arm (by more than GATE_SLACK_PTS), the gate refuses the lever
               and the with-arm result is the without-arm result (saving 0).
* agent-waste- a short agent loop on scripted tool results that never add news;
               without: runs to MAX_STEPS; with: WasteGuard stops it.

Own GPU only (never run here): power caps, quantization, adaptive top-k / tiered experts.

Cost and quality are MEASURED (token counts x list price, fixed answer checks).
Energy is an ESTIMATE from tokens: wattnot.cloud.energy (2 x 42B active params x
(uncached prompt + completion) FLOPs x 1.8e-12..1.8e-11 J/FLOP).

A hard stop: a SpendCap per host plus a combined cap (default $5).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from wattnot.agentwaste.guard import WasteGuard
from wattnot.cloud import energy as cloud_energy
from wattnot.cloud.transport import Reply, Request
from wattnot.runner.workload import WorkloadItem, load_workload
from wattnot.spark_open.h2h import MODEL, BasetenLeg, BudgetStop, ModalLeg, SpendCap

HOSTS = ("baseten", "modal")
# Hosted power/water ESTIMATE. J/token is MEASURED on our H100 (RUN2 baseline,
# data/h100-run2/vm/baseline.json, granite-3.1-3b-a800m, 0.8B active params), scaled by
# active parameters to Muse Spark Open (42B active). Assumption: energy per token scales
# linearly with active parameters on the same class of GPU.
H100_J_PER_TOKEN = 0.14966580932784637
H100_ACTIVE_PARAMS = 0.8e9
SPARK_OPEN_ACTIVE_PARAMS = 42e9
J_PER_TOKEN = H100_J_PER_TOKEN * SPARK_OPEN_ACTIVE_PARAMS / H100_ACTIVE_PARAMS  # about 7.86 J
WATER_L_PER_KWH = 0.36


def saved_estimate(tokens_without: int, tokens_with: int, usd_without: float, usd_with: float) -> dict:
    """tokens saved -> J -> kWh -> L; $ saved is MEASURED (tokens x list price)."""
    tok = tokens_without - tokens_with
    j = tok * J_PER_TOKEN
    kwh = j / 3.6e6
    return {"tokens_saved": {"value": tok, "label": "MEASURED"},
            "j_per_token": {"value": round(J_PER_TOKEN, 4), "label": "ESTIMATE",
                            "method": f"H100 {H100_J_PER_TOKEN:.4f} J/token (RUN2, MEASURED) x 42B/0.8B active params"},
            "energy_j": {"value": round(j, 3), "label": "ESTIMATE"},
            "energy_kwh": {"value": kwh, "label": "ESTIMATE"},
            "water_l": {"value": kwh * WATER_L_PER_KWH, "label": "ESTIMATE", "method": "kWh x 0.36 L/kWh"},
            "usd_saved": {"value": round(usd_without - usd_with, 6), "label": "MEASURED"},
            "power_saved_pct": {"value": round(100 * tok / tokens_without, 2) if tokens_without else None, "label": "ESTIMATE"}}
API_LEVERS = ("routing", "gate", "waste")
OWN_GPU_ONLY = {"caps": "Power caps need root on the GPU; hosted platforms give none.",
                "quant": "Quantization needs our own deployment of the model; not possible on a shared endpoint.",
                "topk": "Adaptive top-k / tiered experts change the model's router; needs our own deployment."}
GATE_SLACK_PTS = 1.0
MAX_STEPS = 8
TOTAL_CAP_USD = 5.0
LEG = {"baseten": BasetenLeg, "modal": ModalLeg}


def _tot(answers) -> dict:
    return {"prompt": sum(a.prompt_tokens for a in answers), "cached": sum(a.cached_tokens for a in answers),
            "completion": sum(a.completion_tokens for a in answers), "usd": sum(a.usd or 0 for a in answers),
            "answers": len(answers), "passed": sum(a.passed for a in answers)}


def _arm(answers, n_items: int) -> dict:
    t = _tot(answers)
    lo, hi = cloud_energy.wh_range(t["prompt"], t["cached"], t["completion"])
    k = 1000 / max(1, n_items)
    return {"pass_pct": {"value": round(100 * t["passed"] / max(1, n_items), 2), "label": "MEASURED"},
            "usd_per_1k": {"value": round(t["usd"] * k, 6), "label": "MEASURED"},
            "wh_per_1k": {"value": round((lo + hi) / 2 * k, 6), "low": round(lo * k, 6), "high": round(hi * k, 6),
                          "label": "ESTIMATE", "method": cloud_energy.METHOD},
            "calls": t["answers"], "tokens": {k_: t[k_] for k_ in ("prompt", "cached", "completion")}}


def _pct(a: float, b: float) -> float | None:
    return None if not a else round(100 * (a - b) / a, 2)


def _routing(transport, items, cap, ab) -> dict:
    base = LEG[ab](transport, effort="medium", cap=cap)
    low = LEG[ab](transport, effort="low", cap=cap)
    without = [base.complete(it) for it in items]
    with_, steps = [], 0
    for it in items:
        a = low.complete(it)
        with_.append(a)
        if not a.passed:
            steps += 1
            with_.append(base.complete(it, rep=1))
    wo, wi = _arm(without, len(items)), _arm(with_, len(items))
    # with-arm pass: an item passes if its last try passed
    last = {}
    for a in with_:
        last[a.item_id] = a.passed
    wi["pass_pct"]["value"] = round(100 * sum(last.values()) / max(1, len(items)), 2)
    return {"without": wo, "with": wi, "stepped_up": steps}


def _agent_body(task: str, steps: int) -> dict:
    msgs = [{"role": "user", "content": task}]
    for i in range(steps):
        call = {"id": f"c{i}", "type": "function", "function": {"name": "search", "arguments": json.dumps({"q": "status"})}}
        msgs.append({"role": "assistant", "content": None, "tool_calls": [call]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "no new results"})
    return {"model": "", "max_tokens": 64, "temperature": 0, "messages": msgs}


def _waste(transport, cap, tasks: Sequence[str]) -> dict:
    model = getattr(transport, "model", None) or MODEL
    out = {}
    for arm in ("without", "with"):
        calls = pt = ct = 0
        usd = 0.0
        for t in tasks:
            g = WasteGuard() if arm == "with" else None
            for step in range(MAX_STEPS):
                body = _agent_body(t, step)
                if g is not None and g.before(body).action == "stop":
                    break
                body["model"] = model
                cap.check((len(json.dumps(body)) / 2 * 1.25 + 64 * 4.25) / 1e6)  # worst case: 2 chars/token
                r: Reply = transport(Request(f"waste|{arm}|{TASKS.index(t) if t in TASKS else 0}|{step}", body))
                if r.status != 200:
                    calls += 1
                    continue
                u = r.body.get("usage") or {}
                calls += 1
                pt += int(u.get("prompt_tokens", 0))
                ct += int(u.get("completion_tokens", 0))
                c = (int(u.get("prompt_tokens", 0)) * 1.25 + int(u.get("completion_tokens", 0)) * 4.25) / 1e6
                usd += c
                cap.add(c)
                if g is not None:
                    g.after(r.body)
        lo, hi = cloud_energy.wh_range(pt, 0, ct)
        out[arm] = {"calls": calls, "tokens": {"prompt": pt, "completion": ct},
                    "usd": {"value": round(usd, 6), "label": "MEASURED"},
                    "wh": {"value": round((lo + hi) / 2, 6), "label": "ESTIMATE", "method": cloud_energy.METHOD}}
    return out


def run_ab(transports: dict, items: Sequence[WorkloadItem], *, tasks: Sequence[str],
           cap_usd: float = TOTAL_CAP_USD, state_dir: Path = Path("reports/hosted-ab")) -> dict:
    res: dict = {"spec": "wattnot.hosted-ab/1", "cap_usd": cap_usd, "own_gpu_only": OWN_GPU_ONLY, "hosts": {}}
    per = cap_usd / max(1, len(transports))
    for host in HOSTS:
        tr = transports.get(host)
        if tr is None:
            res["hosts"][host] = {"status": "not_run", "why": "endpoint not configured or down"}
            continue
        cap = SpendCap(per, state_dir / f"{host}.spend.json")
        try:
            r = _routing(tr, items, cap, host)
            w = _waste(tr, cap, tasks)
        except BudgetStop as e:
            res["hosts"][host] = {"status": "stopped", "why": f"hard stop: {e}"}
            continue
        except Exception as e:  # endpoint down mid-run
            res["hosts"][host] = {"status": "not_run", "why": f"endpoint error: {type(e).__name__}"}
            continue
        wo, wi = r["without"], r["with"]
        gate_ok = wi["pass_pct"]["value"] >= wo["pass_pct"]["value"] - GATE_SLACK_PTS
        rs = _pct(wo["usd_per_1k"]["value"], wi["usd_per_1k"]["value"])
        adopt = gate_ok and rs is not None and rs > 0
        levers = {
            "routing": {"saved_pct": {"value": rs if adopt else 0.0, "label": "MEASURED"},
                        "raw_pct": rs, "adopted": adopt,
                        "note": None if adopt else ("refused by the quality gate" if not gate_ok else "costlier than without, so WattNot keeps the plain setting"),
                        "energy_saved_pct": {"value": _pct(wo["wh_per_1k"]["value"], wi["wh_per_1k"]["value"]) if adopt else 0.0, "label": "ESTIMATE"},
                        "stepped_up": r["stepped_up"]},
            "gate": {"passed": gate_ok, "with_pass_pct": wi["pass_pct"], "without_pass_pct": wo["pass_pct"],
                     "slack_pts": GATE_SLACK_PTS, "saved_pct": {"value": None, "label": "MEASURED"},
                     "note": "the gate saves nothing itself; it refuses a lever that costs quality"},
            "waste": {"saved_pct": {"value": _pct(w["without"]["usd"]["value"], w["with"]["usd"]["value"]), "label": "MEASURED"},
                      "calls_without": w["without"]["calls"], "calls_with": w["with"]["calls"]},
        }
        for k, why in OWN_GPU_ONLY.items():
            levers[k] = {"status": "own_gpu_only", "why": why}
        tw = lambda a: a["tokens"]["prompt"] - a["tokens"]["cached"] + a["tokens"]["completion"]
        fin = wi if adopt else wo
        wt = lambda a: a["tokens"]["prompt"] + a["tokens"]["completion"]
        n1k = 1000 / max(1, len(items))
        est = saved_estimate(int((tw(wo)) * n1k) + wt(w["without"]), int(tw(fin) * n1k) + wt(w["with"]),
                             wo["usd_per_1k"]["value"] + w["without"]["usd"]["value"], fin["usd_per_1k"]["value"] + w["with"]["usd"]["value"])
        est["basis"] = "per 1k answers (routing) plus the agent tasks (waste)"
        res["hosts"][host] = {"status": "ran", "estimate": est, "without": wo, "with": wi if adopt else wo,
                              "gate_passed": gate_ok, "levers": levers, "agent_waste": w,
                              "spent_usd": round(cap.session_usd, 6)}
    res["spent_usd"] = round(sum(h.get("spent_usd", 0) for h in res["hosts"].values()), 6)
    return res


# ---- mock transport for the CPU dry-run (no network, no money) ----
class MockHost:
    def __init__(self, name: str, *, down: bool = False):
        self.name, self.down, self.model = name, down, f"mock/{name}"

    def __call__(self, req: Request) -> Reply:
        if self.down:
            raise ConnectionError(f"{self.name} is down (mock)")
        eff = req.body.get("reasoning_effort", "medium")
        msgs = req.body.get("messages") or []
        prompt = " ".join(str(m.get("content") or "") for m in msgs)
        ct = {"low": 60, "medium": 220}.get(eff, 120)
        text = "4" if "2 + 2" in prompt or "2+2" in prompt else "ok"
        return Reply(200, {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": max(8, len(prompt) // 4), "completion_tokens": ct}}, 0.05)


TASKS = ("Find the status of job 41 and report it.", "Check whether the nightly build passed.", "Look up the latest GPU price.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="wattnot.spark_open.hosted_ab")
    ap.add_argument("--mock", action="store_true", help="CPU dry-run with mock hosts (no network)")
    ap.add_argument("--down", default="", help="comma list of mock hosts to mark down")
    ap.add_argument("--workload", default="workloads/cloud-effort-v1.jsonl")
    ap.add_argument("--items", type=int, default=15)
    ap.add_argument("--cap-usd", type=float, default=TOTAL_CAP_USD)
    ap.add_argument("--out", required=True)
    ap.add_argument("--live", action="store_true", help="paid run on the hosts in --hosts (needs Akash's okay)")
    ap.add_argument("--hosts", default="baseten,modal")
    ap.add_argument("--skip-reason", default="")
    a = ap.parse_args(argv)
    if a.mock == a.live:
        ap.error("pick exactly one of --mock or --live")
    a.hosts = set(filter(None, a.hosts.split(",")))
    trs, notes = {}, {}
    if a.mock:
        down = set(filter(None, a.down.split(",")))
        trs = {h: MockHost(h, down=h in down) for h in HOSTS}
    else:  # live: paid, per-token, behind the hard stop
        from wattnot.cloud.transport import LiveTransport, ModalLiveTransport
        from wattnot.spark_open.h2h import BASE_URL
        if "baseten" in a.hosts:
            trs["baseten"] = LiveTransport(base_url=BASE_URL)  # BASETEN_API_KEY from env, never printed
        if "modal" in a.hosts:
            trs["modal"] = ModalLiveTransport()
        for h in HOSTS:
            if h not in a.hosts:
                notes[h] = a.skip_reason or "not selected"
    items = list(load_workload(Path(a.workload)))[: a.items]
    out = Path(a.out)
    r = run_ab(trs, items, tasks=TASKS, cap_usd=a.cap_usd, state_dir=out.parent / "spend")
    for h, why in notes.items():
        r["hosts"][h] = {"status": "not_run", "why": why}
    r["mode"] = "MOCK" if a.mock else "LIVE"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r, indent=2) + "\n")
    for h, v in r["hosts"].items():
        print(h, v["status"], {k: (x.get("saved_pct") or {}).get("value", x.get("status")) for k, x in v.get("levers", {}).items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
