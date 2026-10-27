"""The whole side demo in one command: fetch, tune, serve, head to head, route, receipt.

    dry run  (CPU, tiny GGUF, Baseten replayed from a recording; costs nothing):
        python -m wattnot.spark_open.side --mode dry --models-dir M --out out/

    real run (one H100 box you already rented; needs the 276 GB GGUF and 250 GB RAM):
        python -m wattnot.spark_open.side --mode real --models-dir /data/spark-open --out out/ \
            --yes-gpu-is-running

The tool never rents a GPU. `--mode real` only starts if you pass `--yes-gpu-is-running`, so
a typo cannot start a long run by accident. Baseten calls are live only with `--baseten live`
and stop at the $0.50 cap (see h2h.py). Output:

    out/tune.json    the tuning tries (hash chained)
    out/h2h.json     both legs, the comparison and the routing choices (hash chained to tune.json)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from wattnot.receipt.canon import canonical_sha256
from wattnot.runner.workload import load_workload, workloads_dir
from wattnot.spark_open import fetch as fetch_mod
from wattnot.spark_open import router, tune
from wattnot.spark_open.bench import LocalRig
from wattnot.spark_open.h2h import DEFAULT_CAP_USD, print_summary, run_h2h
from wattnot.spark_open.knobs import SparkKnobs
from wattnot.spark_open.llama_server import LlamaServer

REAL_SERVER_ARGS = ("--reasoning-effort", "low", "--reasoning-budget", "1500")


def main(argv=None) -> int:  # pragma: no cover - orchestration, covered by the dry-run script
    ap = argparse.ArgumentParser(prog="wattnot.spark_open.side")
    ap.add_argument("--mode", choices=("dry", "real"), default="dry")
    ap.add_argument("--models-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--quant", default=tune.DEFAULT_QUANT)
    ap.add_argument("--planner", choices=("fallback", "mock", "muse"), default="fallback")
    ap.add_argument("--max-tries", type=int, default=tune.MAX_TRIES)
    ap.add_argument("--threads", type=int, default=tune.DEFAULT_THREADS)
    ap.add_argument("--tune-items", type=int, default=6)
    ap.add_argument("--h2h-items", type=int, default=15)
    ap.add_argument("--effort", default="low", choices=("low", "medium", "high", "xhigh"))
    ap.add_argument("--baseten", choices=("off", "replay", "live"), default="replay")
    ap.add_argument("--baseten-cap-usd", type=float, default=DEFAULT_CAP_USD)
    ap.add_argument("--baseten-fixture", default="data/cloud/recorded-smoke.json")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--skip-fetch", action="store_true")
    ap.add_argument("--yes-gpu-is-running", action="store_true")
    a = ap.parse_args(argv)

    real = a.mode == "real"
    if real and not a.yes_gpu_is_running:
        print("real mode needs --yes-gpu-is-running (you start and stop the GPU box yourself)")
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    quant = a.quant
    if real and not a.skip_fetch:
        order = (a.quant, *[q for q in fetch_mod.DEFAULT_ORDER if q != a.quant])
        fr = fetch_mod.fetch(a.models_dir, order=order)
        print("\n".join(fr.steps))
        if not fr.ok:
            print("fetch failed; nothing else was started")
            return 1
        quant = fr.quant  # the quant that fit and verified (IQ1_S if IQ2_S did not)
    wl = workloads_dir() / "cloud-effort-v1.jsonl"
    items = list(load_workload(wl))[: a.tune_items]
    transport = {"fallback": None, "mock": tune.mock_transport}.get(a.planner)
    if a.planner == "muse":
        transport = tune.guarded_transport()
    planner = tune.Planner(transport, source="mock" if a.planner == "mock" else tune.MODEL)
    rig = LocalRig(Path(a.models_dir), items, out, resident_gb=250.0 if real else 1.0)
    label = "MEASURED" if real else "MEASURED (tiny CPU model: plumbing check only)"
    s = tune.run_loop(
        rig,
        out,
        items=items,
        quant=quant,
        planner=planner,
        max_tries=a.max_tries,
        threads=a.threads,
        label=label,
    )
    best = s["best"]
    if best is None:
        print("tune found no working setting; stopping before the head to head")
        return 1
    k = best["knobs"]
    knobs = SparkKnobs(k["quant"], k["n_cpu_moe"], k["ubatch"], k["op_offload"], k["threads"])
    print(f"tune: best n_cpu_moe={knobs.n_cpu_moe} ubatch={knobs.ubatch} tok/s={best['tok_s']}")
    tune_hash = canonical_sha256(json.loads((out / "tune.json").read_text()))

    srv = LlamaServer(
        a.models_dir, port=a.port, log_dir=out / "logs", extra_args=REAL_SERVER_ARGS if real else ()
    )
    try:
        srv.start(knobs)
        wl_items = list(load_workload(wl))[: a.h2h_items]
        r = run_h2h(
            wl,
            wl_items,
            effort=a.effort,
            local_url=srv.base_url,
            tiny=not real,
            baseten=a.baseten,
            fixture=Path(a.baseten_fixture),
            cap_usd=a.baseten_cap_usd,
            threads=knobs.threads,
            resident_gb=250.0 if real else 1.0,
        )
    finally:
        srv.stop()
    r["integrity"]["prev_sha256"] = tune_hash
    r["tuned_with"] = {
        "n_cpu_moe": knobs.n_cpu_moe,
        "ubatch": knobs.ubatch,
        "op_offload": knobs.op_offload,
        "quant": knobs.quant,
    }
    router.attach(r)
    (out / "h2h.json").write_text(json.dumps(r, indent=2) + "\n")
    print_summary(r)
    for d in r["routing"]["decisions"]:
        print(f"route {d['workload']['name']:>18} -> {d['chosen']}  ({d['reason']})")
    print(f"done in {time.monotonic() - t0:.0f} s; wrote {out}/tune.json and {out}/h2h.json")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
