#!/usr/bin/env python3
"""RUN3 add-on: vLLM prefix caching off vs on, interleaved, n>=3, on a repeated-prefix workload.

The workload (workloads/prefix-cache-v1.jsonl) sends the same long system prompt + repo context in
front of every question, which is exactly what ``enable_prefix_caching`` is for. We measure joules
per correct answer, the prefix-cache hit rate (from vLLM's Prometheus metrics), TTFT, p99 and the
quality gate, for both arms.

Dry run anywhere (no GPU, mock numbers, labels MOCK):
    python scripts/prefix_cache_ab.py --fake --out /tmp/prefix-dry
On the GPU host (vLLM is (re)started per arm with/without --enable-prefix-caching):
    python scripts/prefix_cache_ab.py --out /root/run3/results/prefix --model meta-llama/Llama-3.1-8B-Instruct
"""
import argparse, json, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from wattnot.context_saver import prefix_cache as PC


def _metrics(url: str) -> str:  # pragma: no cover - GPU host only
    try:
        return urllib.request.urlopen(url + "/metrics", timeout=5).read().decode()  # noqa: S310
    except OSError:
        return ""


def _fake(out: Path, reps: int) -> dict:
    summ = PC.mock_ab(reps)
    summ["label"] = "MOCK"
    summ["workload"] = "workloads/prefix-cache-v1.jsonl"
    (out / "prefix_cache_summary.json").write_text(json.dumps(summ, indent=1))
    return summ


def _real(out: Path, o) -> dict:  # pragma: no cover - needs a GPU and vLLM
    from wattnot.proof import run as R
    url = f"http://127.0.0.1:{o.port}"
    extra = ["step", "--out", str(out), "--model", o.model, "--port", o.port,
             "--profile", o.profile, "--workload", o.workload]
    # Reuse phase_ab's trick: resolve the proof profile into a rig args namespace.
    import scripts.phase_ab as PA
    a = PA.rig_args(extra)

    def trial(on: bool, rep: int) -> dict:
        srv = R.serve(o.model, int(o.port), out / f"vllm-{'on' if on else 'off'}.log",
                      extra=(["--enable-prefix-caching"] if on else ["--no-enable-prefix-caching"]))
        try:
            R.wait_ready(url)
            m0 = _metrics(url)
            rig, guard = R.open_rig(a)
            try:
                b = rig.block(700)
            finally:
                guard.__exit__(None, None, None)
            m1 = _metrics(url)
            return {"j_per_req": b.get("j_per_req"), "pass_pct": b.get("pass_pct"),
                    "ttft_ms": b.get("ttft_ms"), "p99_ms": (b.get("p99_s") or 0) * 1000,
                    "cache_hit_rate": PC.hit_rate_from_metrics(m0, m1)}
        finally:
            srv.terminate()
            time.sleep(2)

    summ = PC.run_ab(trial, o.reps, label="MEASURED")
    summ["workload"] = o.workload
    (out / "prefix_cache_summary.json").write_text(json.dumps(summ, indent=1))
    return summ


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default="meta-llama/Llama-3.1-8B-Instruct")
    ap.add_argument("--port", default="8000")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--profile", default="standard")
    ap.add_argument("--workload", default="workloads/prefix-cache-v1.jsonl")
    ap.add_argument("--fake", action="store_true")
    o = ap.parse_args(argv)
    o.out.mkdir(parents=True, exist_ok=True)
    summ = _fake(o.out, o.reps) if o.fake else _real(o.out, o)
    print("PREFIX_CACHE_JSON " + json.dumps(summ))
    h = summ["headline"]
    print(f"prefix caching: J/correct {h['j_per_correct_change_pct']}% , "
          f"hit rate off {h['cache_hit_rate']['off']} -> on {h['cache_hit_rate']['on']}, "
          f"gate {'passed' if summ['kept'] else 'FAILED'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
