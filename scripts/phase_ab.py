#!/usr/bin/env python3
"""RUN3 add-on: phase-aware clock hook vs best fixed cap (350 W) vs stock, interleaved, n>=3.

On the GPU host (vLLM already serving on --port), as root:
    python scripts/phase_ab.py --out /root/run2/results/phase --model meta-llama/Llama-3.1-8B-Instruct
Dry run anywhere (fake GPU, mock NVML, labels MOCK):
    python scripts/phase_ab.py --fake --out /tmp/phase-dry
Arms per round, order rotated each round: stock (700 W), cap350, phase (700 W + clock sidecar).
A short tune first picks the decode clock (two candidates). Clocks are reset after every phase block.
Granularity: per scheduler window of batch steps (100 ms), not per request. Prior art: DynamoLLM.
"""
import argparse, json, os, statistics as st, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from wattnot.proof import run as R
from wattnot.control import phase_planner as PP

def rig_args(extra):
    box = {}
    orig = argparse.ArgumentParser.parse_args
    def grab(self, argv=None, ns=None):
        box["a"] = orig(self, argv, ns); raise SystemExit(0)
    argparse.ArgumentParser.parse_args = grab
    try: R.main(extra)
    except SystemExit: pass
    finally: argparse.ArgumentParser.parse_args = orig
    a = box["a"]
    prof = R.resolve_profile(a.profile, **{k: getattr(a, k) for k in R.prof_fields()})
    for k, v in prof.as_dict().items(): setattr(a, k, v)
    return a

def _spark_transport():
    """Real Spark transport through the spend guard, or None (take the sweep) if no key is set."""
    if not os.environ.get("META_API_KEY"):
        return None
    from wattnot.meta.client import GuardedClient, load_key
    client = GuardedClient(key=load_key())
    return lambda body: client(body, purpose="phase-planner")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default="meta-models/Muse-Glimmer-30B"); ap.add_argument("--port", default="8000")
    ap.add_argument("--reps", type=int, default=3); ap.add_argument("--cap", type=int, default=350)
    ap.add_argument("--high", type=int, default=1980); ap.add_argument("--lows", default="1410,1590")
    ap.add_argument("--profile", default="standard"); ap.add_argument("--fake", action="store_true"); ap.add_argument("--serve", action="store_true")
    o = ap.parse_args(); o.out.mkdir(parents=True, exist_ok=True)
    extra = ["step", "--out", str(o.out), "--model", o.model, "--port", o.port, "--profile", o.profile]
    if o.fake: extra += ["--fake", "--fake-delay", "0", "--profile", "quick"]
    a = rig_args(extra)
    proc = R.serve(o.model, int(o.port), o.out / "vllm.log") if o.serve and not o.fake else None
    smi = None if o.fake else subprocess.Popen(["nvidia-smi", "--query-gpu=timestamp,power.draw,clocks.sm,clocks.mem,temperature.gpu",
                                                "--format=csv,noheader", "-lms", "1000"], stdout=open(o.out / "nvidia_smi_1hz.csv", "w"))
    rig, guard = R.open_rig(a)
    label = "MOCK" if o.fake else "MEASURED"
    url = f"http://127.0.0.1:{o.port}"
    rows = []; f = open(o.out / "phase_blocks.jsonl", "a")
    def phase_block(low, rep, kind, ratio=4.0):
        logp = o.out / f"phase-{kind}-{rep}-{low}.jsonl"
        cmd = [sys.executable, "-m", "wattnot.control.phase_clock", "--url", url, "--high", str(o.high),
               "--low", str(low), "--ratio", str(ratio), "--log", str(logp), "--summary", str(logp) + ".summary.json"] + (["--mock"] if o.fake else [])
        sc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try: b = rig.block(700)
        finally:
            sc.terminate(); sc.wait(30)
        return b
    def rec(arm, rep, b, **kw):
        r = dict(arm=arm, rep=rep, label=label, j_per_req=b.get("j_per_req"), pass_pct=b.get("pass_pct"),
                 p99_s=b.get("p99_s"), mean_w=b.get("mean_w"), t=time.time(), **kw)
        r["j_per_correct"] = r["j_per_req"] / (r["pass_pct"] / 100) if r["j_per_req"] and r["pass_pct"] else None
        rows.append(r); f.write(json.dumps(r) + "\n"); f.flush(); print(json.dumps(r), flush=True); return r
    try:
        lows = [int(x) for x in o.lows.split(",")]
        tune = [rec("tune", 0, phase_block(l, 0, "tune"), low_mhz=l) for l in lows]
        ok = [t for t in tune if t["j_per_correct"]] or tune
        sweep_low = min(ok, key=lambda t: t["j_per_correct"] or 9e9)["low_mhz"]
        # Muse Spark picks the thresholds; validate, clamp, and fall back to the sweep.
        base_j = next((t["j_per_correct"] for t in tune if t["j_per_correct"]), 0.0) or 0.0
        p99 = next((t["p99_s"] for t in tune if t["p99_s"]), 4.0) or 4.0
        summary = PP.summarize(high_cap_mhz=o.high, sweep_lows=lows, base_j_per_req=base_j,
                               p99_budget_ms=int(p99 * 1100), decode_share_pct=60.0)
        transport = PP.MockSparkTransport() if o.fake else _spark_transport()
        trace_path = o.out / "phase_thresholds.trace.jsonl"
        def _trace(t):
            with open(trace_path, "a") as fh:
                fh.write(json.dumps(t) + "\n")
        thr = PP.choose(summary, transport=transport, on_trace=_trace)
        low, high, ratio = thr.low_mhz, thr.high_mhz, thr.prefill_ratio
        o.high = high
        arms = ["stock", "cap", "phase"]
        for rep in range(1, o.reps + 1):
            order = arms[rep % 3:] + arms[:rep % 3]
            for arm in order:
                if arm == "stock": rec("stock", rep, rig.block(700))
                elif arm == "cap": rec(f"cap{o.cap}", rep, rig.block(o.cap))
                else: rec("phase", rep, phase_block(low, rep, "ab", ratio), low_mhz=low, high_mhz=o.high)
    finally:
        try: rig.set_cap(700)
        except Exception: pass
        guard.__exit__(None, None, None)
        if smi: smi.terminate()
        if proc: proc.terminate()
    summ = {"label": label, "low_mhz": low, "high_mhz": o.high, "prefill_ratio": ratio, "reps": o.reps,
            "granularity": "per scheduler window of batch steps (100 ms), not per request",
            "prior_art": "DynamoLLM",
            "thresholds": {"source": thr.source, "clamped": thr.clamped, "sweep_low_mhz": sweep_low,
                           "reason": thr.reason,
                           "how": "Muse Spark picks prefill_ratio/high_mhz/low_mhz via the planner, "
                                  "validated and clamped; the 2-point sweep is the fallback"},
            "cross_check": "nvidia_smi_1hz.csv vs NVML energy counter", "arms": {}}
    for arm in sorted({r["arm"] for r in rows if r["arm"] != "tune"}):
        rs = [r for r in rows if r["arm"] == arm]
        def ms(k):
            v = [r[k] for r in rs if r[k] is not None]
            return {"mean": st.mean(v) if v else None, "sd": st.stdev(v) if len(v) > 1 else None, "n": len(v)}
        summ["arms"][arm] = {k: ms(k) for k in ("j_per_req", "j_per_correct", "pass_pct", "p99_s", "mean_w")}
    (o.out / "phase_summary.json").write_text(json.dumps(summ, indent=1)); print("PHASE_JSON " + json.dumps(summ))
    return 0

if __name__ == "__main__": raise SystemExit(main())
