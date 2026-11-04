"""Headless Go-live side by side on a hosted provider (Modal or Baseten): starts the dashboard with
the gateway mounted, runs each workload without vs with WattNot (sequential), saves the results.

Power cap: not available on these providers (no root), so the cap step is skipped and labelled.
Energy: NVML counter inside a Modal GPU container (MEASURED) or tokens x J/token (ESTIMATE).

    PYTHONPATH=src python scripts/e2e/hosted_gateway_run.py --provider baseten \
        --config configs/gw-baseten.yaml --workloads text,agent --n 60 --max-usd 5
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def _req(port, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method="POST" if data else "GET",
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as f:  # noqa: S310
        return json.loads(f.read().decode())


def spend_line(provider: str, row: dict, ledger: Path) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "provider": provider, **row}) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True, choices=["baseten", "modal"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--workloads", default="text,agent")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--port", type=int, default=8797)
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-usd", type=float, default=5.0, help="hard stop per workload run (both arms)")
    ap.add_argument("--timeout-min", type=float, default=60, help="hard stop per workload run")
    ap.add_argument("--ledger", default="data/spend/{provider}.ledger.jsonl")
    a = ap.parse_args(argv)
    out = Path(a.out or f"out/hosted/{a.provider}")
    out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "WATTNOT_SAVINGS_LOG": str(out / "savings.jsonl"), "WATTNOT_NODE_LOG": str(out / "node.jsonl"),
           "WATTNOT_PLUG_YAML": str(out / "plug-unused.yaml"), "WATTNOT_SEQ_MAX_USD": str(a.max_usd),
           "PYTHONPATH": os.environ.get("PYTHONPATH", "src")}
    log = open(out / "dash.log", "a")
    srv = subprocess.Popen([sys.executable, "-m", "wattnot.dash", "--port", str(a.port), "--gateway", a.config],
                           env=env, stdout=log, stderr=subprocess.STDOUT)
    results = []
    try:
        for _ in range(120):
            try:
                _req(a.port, "/api/dash/gateway/status"); break
            except OSError:
                time.sleep(1)
        print("gateway:", json.dumps({k: v for k, v in _req(a.port, "/api/dash/gateway/start", {}).items()
                                      if k in ("running", "upstream", "url")}), flush=True)
        for wl in [w for w in a.workloads.split(",") if w]:
            t0 = time.time()
            st = _req(a.port, "/api/dash/traffic/start", {"workload": wl, "n": a.n, "speed": "fast",
                                                         "with_without": True, "mode": "test", "confirm_over_1": True})
            print(f"[{wl}] started sequential={st.get('sequential')}", flush=True)
            last = None
            while True:
                time.sleep(5)
                s = _req(a.port, "/api/dash/traffic/status")
                ph = (s.get("phase"), s.get("done"), s.get("total"))
                if ph != last:
                    print(f"[{wl}] {ph[0]} {ph[1]}/{ph[2]} usd={s.get('seq_usd')}", flush=True); last = ph
                if not s.get("running"):
                    break
                if time.time() - t0 > a.timeout_min * 60:
                    _req(a.port, "/api/dash/traffic/stop", {}); print(f"[{wl}] hard stop: time", flush=True)
            r = json.loads((out / "sequential.json").read_text())
            r["stopped"] = s.get("stopped")
            row = {"workload": wl, "run_id": r.get("run_id"), "usd_estimate": s.get("seq_usd"),
                   "seconds": round(time.time() - t0, 1), "hard_stop_usd": a.max_usd, "label": "ESTIMATE"}
            spend_line(a.provider, row, Path(a.ledger.format(provider=a.provider)))
            results.append(r)
            print(f"[{wl}] " + json.dumps({k: r.get(k) for k in ("run_id", "energy_saved_pct", "energy_label",
                                                                    "agreement", "no_worse", "cap_available", "error", "stopped")}), flush=True)
        for name, path in (("eu", "/api/dash/plug/eu"), ("quality", "/api/dash/plug/quality")):
            try:
                (out / f"{name}.json").write_text(json.dumps(_req(a.port, path), indent=1))
            except Exception as e:  # noqa: BLE001
                print(f"{name}: {type(e).__name__}", flush=True)
    finally:
        srv.terminate()
        srv.wait(20)
    summ = [{k: v for k, v in r.items() if k not in ("with", "without")} | {
        arm: {k: (r.get(arm) or {}).get(k) for k in ("joules", "requests", "correct", "scored", "usd", "tokens", "label", "seconds")}
        for arm in ("without", "with")} for r in results]
    (out / "summary.json").write_text(json.dumps(summ, indent=1))
    print("summary:", out / "summary.json")
    return 0 if results and all("error" not in r for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
