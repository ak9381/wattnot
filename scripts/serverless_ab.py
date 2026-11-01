#!/usr/bin/env python3
"""Serverless-style tuning on one GPU: is it cheaper to keep Glimmer warm or scale to zero?

Per rep it measures, with the NVML energy counter:
  * warm idle  -- the server is up and loaded, no traffic, for --idle-s seconds (W);
  * zero idle  -- the server is stopped (scale to zero), the card sits idle for --idle-s (W);
  * cold start -- start the server (scripts/glimmer_serve.sh) and answer one request: seconds
    and joules from "start" to the first answer.
From those it reports the break-even gap: scale to zero only pays when the gap between requests
is longer than cold_start_J / (warm_W - zero_W) seconds. It leaves the BF16 server running.

    python scripts/serverless_ab.py --out /root/run2/results/t2g-serverless --reps 3
    python scripts/serverless_ab.py --fake --out runs/t2g-serverless      # SIMULATED, no GPU
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = "meta-models/Muse-Glimmer-30B"


def _ask(base_url: str, timeout: float) -> bool:
    body = json.dumps({"model": MODEL, "max_tokens": 64,
                       "messages": [{"role": "user", "content": "Say ok."}]}).encode()
    req = urllib.request.Request(f"{base_url}/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return bool(json.load(r).get("choices"))
    except Exception:  # noqa: BLE001 - a failed answer is reported, not raised
        return False


def break_even_s(cold_j: float, warm_w: float, zero_w: float) -> float | None:
    """Seconds of idle gap above which scaling to zero saves energy (None if warm idle is not
    above zero idle, so scaling to zero never pays)."""
    d = warm_w - zero_w
    return round(cold_j / d, 1) if d > 0 else None


def summarize(reps: list[dict], label: str) -> dict:
    def med(k):
        return round(statistics.median(r[k] for r in reps), 2)
    out = {"label": label, "reps": len(reps), "warm_idle_w": med("warm_w"),
           "zero_idle_w": med("zero_w"), "cold_start_s": med("cold_s"),
           "cold_start_j": med("cold_j"), "cold_start_ok": all(r["cold_ok"] for r in reps)}
    out["break_even_gap_s"] = break_even_s(out["cold_start_j"], out["warm_idle_w"],
                                           out["zero_idle_w"])
    out["rounds"] = reps
    return out


def run_real(o) -> dict:  # pragma: no cover - needs the GPU host
    import pynvml
    pynvml.nvmlInit()
    h = pynvml.nvmlDeviceGetHandleByIndex(0)

    def energy_j():
        return pynvml.nvmlDeviceGetTotalEnergyConsumption(h) / 1000.0

    def idle_w(seconds):
        e0, t0 = energy_j(), time.time()
        time.sleep(seconds)
        return (energy_j() - e0) / (time.time() - t0)

    reps = []
    for rep in range(o.reps):
        warm = idle_w(o.idle_s)
        subprocess.run("pkill -f 'vllm serve'; docker rm -f glimmer", shell=True,
                       capture_output=True)
        time.sleep(10)
        zero = idle_w(o.idle_s)
        e0, t0 = energy_j(), time.time()
        up = subprocess.run(["bash", str(ROOT / "scripts" / "glimmer_serve.sh"), "bf16"],
                            capture_output=True, text=True).returncode == 0
        ok = up and _ask(o.base_url, o.req_timeout)
        cold_s, cold_j = time.time() - t0, energy_j() - e0
        reps.append({"rep": rep, "warm_w": round(warm, 1), "zero_w": round(zero, 1),
                     "cold_s": round(cold_s, 1), "cold_j": round(cold_j, 1), "cold_ok": ok})
        print(f"[serverless] rep{rep}: warm {warm:.0f} W, zero {zero:.0f} W, cold start "
              f"{cold_s:.0f} s / {cold_j:.0f} J, ok={ok}", flush=True)
        if not ok:
            break
    return summarize(reps, "MEASURED")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--idle-s", type=float, default=120)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    ap.add_argument("--req-timeout", type=float, default=300)
    ap.add_argument("--fake", action="store_true", help="SIMULATED numbers, no GPU")
    o = ap.parse_args(argv)
    o.out.mkdir(parents=True, exist_ok=True)
    if o.fake:
        reps = [{"rep": i, "warm_w": 115.0, "zero_w": 70.0, "cold_s": 240.0, "cold_j": 52000.0,
                 "cold_ok": True} for i in range(o.reps)]
        s = summarize(reps, "SIMULATED")
    else:
        s = run_real(o)
    (o.out / "serverless_summary.json").write_text(json.dumps(s, indent=1))
    print(json.dumps({k: v for k, v in s.items() if k != "rounds"}))
    return 0 if s.get("cold_start_ok") else 1


if __name__ == "__main__":
    sys.exit(main())
