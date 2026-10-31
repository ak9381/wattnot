#!/usr/bin/env python3
"""RUN3 add-on: vLLM prefix caching off vs on, interleaved, n>=3, on a repeated-prefix workload.

The workload (workloads/prefix-cache-v1.jsonl) sends the same long system prompt + repo context in
front of every question, which is exactly what ``enable_prefix_caching`` is for. We measure joules
per correct answer, the prefix-cache hit rate (from vLLM's Prometheus metrics), TTFT, p99 and the
quality gate, for both arms.

Dry run anywhere (no GPU, mock numbers, labels MOCK):
    python scripts/prefix_cache_ab.py --fake --out /tmp/prefix-dry
On the GPU host (vLLM is (re)started per arm with/without --enable-prefix-caching):
    python scripts/prefix_cache_ab.py --out runs/g6b       # Glimmer via scripts/glimmer_serve.sh
"""
import argparse, json, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))  # for `import scripts.phase_ab`
from wattnot.context_saver import prefix_cache as PC


def _metrics(url: str) -> str:  # pragma: no cover - GPU host only
    try:
        return urllib.request.urlopen(url + "/metrics", timeout=5).read().decode()  # noqa: S310
    except OSError:
        return ""


def save_arm_raw(out: Path, on: bool, rep: int, block: dict) -> Path:
    """Write one arm/rep's raw answer texts to out/raw/prefix-<arm>-r<rep>.jsonl (item G).

    The grader scores pooled copies; keeping the raw per-request answers next to the summary makes a
    wrong pass rate auditable (which answers were cut, which were wrong) for both the prefix A/B and
    the combined block, which share this trial path.
    """
    from wattnot.proof.session import save_raw
    tag = f"prefix-{'on' if on else 'off'}-r{rep}"
    save_raw(out, tag, block)
    return out / "raw" / f"{tag}.jsonl"


def parse_cap(v) -> int | None:
    """A positive int cap, or None (so an unresolved {BEST_CAP} / 'NONE' skips the combined block)."""
    try:
        w = int(v)
    except (TypeError, ValueError):
        return None
    return w if w > 0 else None


def annotate_combined(summ: dict, cap_on: int, cap_off: int) -> dict:
    """Label a combined-block summary: the headline J/correct change is the combined saving %
    (best cap + prefix ON vs stock baseline + prefix OFF). Negative = a saving."""
    summ["mode"] = "combined"
    summ["caps"] = {"on_prefix_w": cap_on, "off_baseline_w": cap_off}
    summ["combined_saving_pct"] = summ["headline"]["j_per_correct_change_pct"]
    return summ


def _fake(out: Path, o) -> dict:
    summ = PC.mock_ab(o.reps)
    summ["label"] = "MOCK"
    summ["workload"] = o.workload
    if getattr(o, "combined", False):
        annotate_combined(summ, o.cap_on_w, o.cap_off_w)
    (out / "prefix_cache_summary.json").write_text(json.dumps(summ, indent=1))
    return summ


def _real(out: Path, o) -> dict:  # pragma: no cover - needs a GPU and vLLM
    from wattnot.proof import run as R
    url = f"http://127.0.0.1:{o.port}"
    extra = ["step", "--out", str(out), "--model", o.model, "--port", o.port,
             "--profile", o.profile, "--workload", o.workload,
             "--reasoning-floor", str(o.reasoning_floor)]
    # Reuse phase_ab's trick: resolve the proof profile into a rig args namespace.
    import scripts.phase_ab as PA
    a = PA.rig_args(extra)

    def serve(on: bool):
        """(Re)start the server for one arm. Glimmer runs in its container via
        glimmer_serve.sh (same flags as every other block); plain vLLM for other models."""
        import os
        import subprocess
        flag = "--enable-prefix-caching" if on else "--no-enable-prefix-caching"
        log = out / f"vllm-{'on' if on else 'off'}.log"
        if "Glimmer" in o.model:
            rc = subprocess.run(["bash", str(ROOT / "scripts" / "glimmer_serve.sh"), "bf16",
                                 str(o.port)], env={**os.environ, "GLIMMER_EXTRA_ARGS": flag,
                                                    "LOG": str(out)}).returncode
            if rc != 0:
                raise RuntimeError(f"glimmer_serve.sh exited {rc} for prefix caching {flag}")
            return None
        argv = ["vllm", "serve", o.model, "--port", str(o.port), "--max-model-len", "4096",
                "--gpu-memory-utilization", "0.90", "--served-model-name", o.model, flag]
        p = subprocess.Popen(argv, stdout=log.open("ab"), stderr=subprocess.STDOUT)
        R.wait_http(f"{url}/v1/models", 900)
        return p

    def trial(on: bool, rep: int) -> dict:
        srv = serve(on)
        try:
            m0 = _metrics(url)
            rig, guard = R.open_rig(a)
            try:
                # combined block: the prefix-ON arm runs at the best passing cap, the baseline at
                # stock. The plain prefix A/B measures the prefix effect at stock on both arms.
                b = rig.block(o.cap_on_w if on else o.cap_off_w)
            finally:
                guard.__exit__(None, None, None)
            save_arm_raw(out, on, rep, b)   # keep this arm/rep's raw answer texts
            m1 = _metrics(url)
            rtok, ctok = b.get("reasoning_tokens") or 0, b.get("tokens") or 0
            return {"j_per_req": b.get("j_per_req"), "pass_pct": b.get("pass_pct"),
                    "ttft_ms": PC.ttft_ms_from_metrics(m0, m1), "p99_ms": (b.get("p99_s") or 0) * 1000,
                    "cache_hit_rate": PC.hit_rate_from_metrics(m0, m1),
                    "reasoning_token_share": round(rtok / ctok, 4) if ctok else None,
                    "reasoning_measured": bool(b.get("reasoning_measured"))}
        finally:
            if srv is not None:
                srv.terminate()
                srv.wait(timeout=60)
            time.sleep(2)

    summ = PC.run_ab(trial, o.reps, label="MEASURED")
    summ["workload"] = o.workload
    if getattr(o, "combined", False):
        annotate_combined(summ, o.cap_on_w, o.cap_off_w)
    (out / "prefix_cache_summary.json").write_text(json.dumps(summ, indent=1))
    return summ


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default="meta-models/Muse-Glimmer-30B")
    ap.add_argument("--port", default="8000")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--profile", default="standard")
    ap.add_argument("--workload", default="workloads/prefix-cache-v1.jsonl")
    ap.add_argument("--reasoning-floor", type=int, default=1024,
                    help="min completion tokens per request so Glimmer's answers are not cut off")
    ap.add_argument("--combined", action="store_true",
                    help="combined block: best passing cap + prefix ON vs stock baseline + prefix "
                         "OFF (interleaved); reports the combined saving %%")
    ap.add_argument("--cap-on", default="700",
                    help="combined: cap for the prefix-ON arm (the best passing cap from the "
                         "sweep); NONE/0 skips the block when the sweep found no passing cap")
    ap.add_argument("--cap-off", type=int, default=700, help="combined: baseline (stock) cap")
    ap.add_argument("--fake", action="store_true")
    o = ap.parse_args(argv)
    if o.combined:
        cap_on = parse_cap(o.cap_on)
        if cap_on is None:
            print("combined block skipped: no passing cap from the sweep "
                  f"(cap-on={o.cap_on!r} is not a cap)")
            return 0
        o.cap_on_w, o.cap_off_w = cap_on, o.cap_off
    else:
        o.cap_on_w = o.cap_off_w = 700   # the plain prefix A/B runs both arms at stock
    o.out.mkdir(parents=True, exist_ok=True)
    summ = _fake(o.out, o) if o.fake else _real(o.out, o)
    print("PREFIX_CACHE_JSON " + json.dumps(summ))
    h = summ["headline"]
    rs = h.get("reasoning_token_share") or {}
    lever = "combined (best cap + prefix)" if o.combined else "prefix caching"
    print(f"{lever}: J/correct {h['j_per_correct_change_pct']}% , "
          f"hit rate off {h['cache_hit_rate']['off']} -> on {h['cache_hit_rate']['on']}, "
          f"reasoning share {rs.get('on')}, gate {'passed' if summ['kept'] else 'FAILED'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
