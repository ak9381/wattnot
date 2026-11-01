#!/usr/bin/env python3
"""Agent-waste saver on the H100: the same agent workload against local Glimmer, saver OFF vs ON.

The bench harness (react / stop-rule / planner loops over the ticket world, the cases with
loops, error storms and paraphrase) drives Muse Glimmer served by vLLM on this GPU. Each arm
runs every case once per rep; arms are interleaved (off, on, on, off, ...) and the NVML energy
counter is read around each arm, so the saving is GPU joules actually not spent:

    joules saved = J(off) - J(on), and J per successful run for both arms.

Cheaper-model routing needs a second, smaller model on the card; with Glimmer alone it is not
measured here (reported as "not measured", never estimated into the joules).

    python scripts/agentwaste_gpu_ab.py --out /root/run2/results/t2h-agentwaste --reps 2
    python scripts/agentwaste_gpu_ab.py --fake --out runs/t2h-agentwaste   # SIMULATED, no GPU
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
MODEL = "meta-models/Muse-Glimmer-30B"
ARMS = ("harness", "harness+saver")


def local_client(base_url: str, timeout: float = 600):
    url = base_url.rstrip("/") + "/chat/completions"

    def call(body, *, purpose: str = "agentwaste-gpu") -> dict:
        b = {k: v for k, v in dict(body).items() if k not in ("reasoning_effort",)}
        b["model"] = MODEL
        b.setdefault("max_tokens", 2048)
        req = urllib.request.Request(url, data=json.dumps(b).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return json.load(r)
    return call


def order(reps: int) -> list[tuple[str, int]]:
    out = []
    for r in range(reps):
        arms = ARMS if r % 2 == 0 else ARMS[::-1]
        out += [(a, r) for a in arms]
    return out


def summarize(rounds: list[dict], label: str) -> dict:
    def arm(a):
        rs = [x for x in rounds if x["arm"] == a]
        j = sum(x["joules"] for x in rs)
        ok = sum(x["successes"] for x in rs)
        calls = sum(x["calls"] for x in rs)
        return {"joules": round(j, 1), "successes": ok, "runs": sum(x["runs"] for x in rs),
                "calls": calls, "j_per_success": round(j / ok, 1) if ok else None}
    off, on = arm("harness"), arm("harness+saver")
    saved = off["joules"] - on["joules"]
    return {"label": label, "model": MODEL, "off": off, "on": on,
            "joules_saved": round(saved, 1),
            "saved_pct": round(100 * saved / off["joules"], 2) if off["joules"] else None,
            "routing": "not measured (one model on the card)", "rounds": rounds}


def run_real(o) -> dict:  # pragma: no cover - needs the GPU host
    import pynvml

    from wattnot.agentwaste.bench.cases import all_cases
    from wattnot.agentwaste.bench.harness import run_case
    pynvml.nvmlInit()
    h = pynvml.nvmlDeviceGetHandleByIndex(0)
    client = local_client(o.base_url)
    cases = all_cases()
    rounds, raw = [], []
    for a, rep in order(o.reps):
        e0, t0 = pynvml.nvmlDeviceGetTotalEnergyConsumption(h), time.time()
        res = [run_case(c, client, a, rep=rep, purpose="agentwaste-gpu") for c in cases]
        joules = (pynvml.nvmlDeviceGetTotalEnergyConsumption(h) - e0) / 1000.0
        rounds.append({"arm": a, "rep": rep, "joules": joules, "wall_s": time.time() - t0,
                       "runs": len(res), "successes": sum(bool(r.success) for r in res),
                       "calls": sum(r.calls for r in res)})
        raw += [{"arm": a, "rep": rep, **{k: v for k, v in vars(r).items()
                                         if isinstance(v, (int, float, str, bool))}} for r in res]
        print(f"[agentwaste-gpu] {a} rep{rep}: {joules:.0f} J, "
              f"{rounds[-1]['successes']}/{len(res)} ok", flush=True)
    (o.out / "agentwaste_gpu_raw.json").write_text(json.dumps(raw))
    return summarize(rounds, "MEASURED")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    ap.add_argument("--fake", action="store_true", help="SIMULATED numbers, no GPU")
    o = ap.parse_args(argv)
    o.out.mkdir(parents=True, exist_ok=True)
    if o.fake:
        rounds = [{"arm": a, "rep": r, "joules": 90000.0 if a == "harness" else 70000.0,
                   "wall_s": 600.0, "runs": 20, "successes": 15, "calls": 200}
                  for a, r in order(o.reps)]
        s = summarize(rounds, "SIMULATED")
    else:
        s = run_real(o)
    (o.out / "agentwaste_gpu_summary.json").write_text(json.dumps(s, indent=1))
    print(json.dumps({k: v for k, v in s.items() if k != "rounds"}))
    if not (s["off"]["calls"] and s["on"]["calls"]):   # every request errored: a failure, not a 0% result
        print("[agentwaste-gpu] no model call succeeded; see agentwaste_gpu_raw.json", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
