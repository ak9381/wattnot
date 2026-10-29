"""One command for the whole proof run, on a GPU host:

    bash scripts/h100_proof.sh --model meta-llama/Llama-3.1-8B-Instruct --out results/proof

Stages: preflight (does the gate catch a bad setting?) -> planner picks caps (Muse Spark when a
Meta key is available, otherwise a bisect rule) -> a fixed grid fills the rest -> report.
Every block's replies are saved, hashed and listed in MANIFEST.sha256.

`step`, `control` and `report` are also separate commands so a planner on another machine can
drive the GPU host over SSH (the Meta key then never leaves that machine).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import signal
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from wattnot.proof.client import ChatClient
from wattnot.proof.decisions import (
    append_record,
    format_decisions_summary,
    load_records,
    new_record,
    settle_record,
    summarize_decisions,
)
from wattnot.proof.planner import BisectPlanner, allowed_caps
from wattnot.proof.profile import add_args as add_profile_args
from wattnot.proof.profile import resolve as resolve_profile
from wattnot.proof.rig import Rig
from wattnot.proof.session import run_control, run_step
from wattnot.runner.workload import load_workload, workloads_dir

DEFAULT_GRID = (600, 500, 400, 350, 300)
MODEL = "meta-llama/Llama-3.1-8B-Instruct"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def make_log(out: Path) -> Callable[[str, dict], None]:
    out.mkdir(parents=True, exist_ok=True)

    def log(kind: str, data: dict) -> None:
        line = json.dumps({"t": now(), "kind": kind, **data}, ensure_ascii=False)
        with (out / "log.jsonl").open("a") as f:
            f.write(line + "\n")
        print(line[:300], flush=True)
    return log


def history_row(s: dict) -> dict:
    return {"cap_w": s["cap_w"], "clock_profile": s.get("clock_profile", "base"),
            "accepted": s["accepted"], "why_not": s["why_not"],
            "energy_change_pct": round(s["energy_change_pct"]["mean"], 2),
            "p99_change_pct": round(s["p99_change_pct"]["mean"], 2),
            "quality_pass_pct": round(s["quality"]["cap_pct"], 1),
            "stock_pass_pct": round(s["quality"]["stock_pct"], 1)}


def run_session(step: Callable[[int], dict], planner, grid: Sequence[int], log, *,
                max_proposals: int = 4, deadline: float | None = None,
                out: Path | None = None) -> list[dict]:
    """Planner picks up to `max_proposals` caps, then the grid fills any cap not yet measured.
    `step(cap)` measures one cap and returns its summary. When `out` is given, each planner
    proposal is appended as one JSON line to ``out/decisions.jsonl`` with its gate outcome."""
    done: list[dict] = []
    chosen_by: dict[int, str] = {}
    step_no = 0

    def over() -> bool:
        return deadline is not None and time.time() > deadline

    def record(prop: dict, hist: list[dict]) -> dict | None:
        if out is None:
            return None
        nonlocal step_no
        step_no += 1
        lo, hi = getattr(planner, "lo", 300), getattr(planner, "hi", 700)
        tried = [h["cap_w"] for h in hist]
        return new_record(step_no, allowed=allowed_caps(lo, hi, tried), proposal=prop)

    for _ in range(max_proposals):
        if over():
            log("deadline", {"stage": "planner"})
            break
        hist = [history_row(s) for s in done]
        prop = planner.propose(hist)
        rec = record(prop, hist)
        log("proposal", {**prop, "history_len": len(hist),
                         "planner": getattr(planner, "name", "?")})
        if prop["kind"] == "stop":
            if rec is not None:
                append_record(out, settle_record(rec, None))
            break
        cap = int(prop["cap_w"])
        s = step(cap)
        s["chosen_by"] = prop.get("by", getattr(planner, "name", "planner"))
        s["planner_reason"] = prop.get("reason")
        chosen_by[cap] = s["chosen_by"]
        log("decision", {"cap_w": cap, "accepted": s["accepted"], "why_not": s["why_not"],
                         "energy_change_pct": s["energy_change_pct"]["mean"],
                         "p99_change_pct": s["p99_change_pct"]["mean"],
                         "quality": s["quality"]["cap_pct"], "chosen_by": s["chosen_by"]})
        done.append(s)
        if rec is not None:
            append_record(out, settle_record(rec, s))
    for cap in grid:
        if over():
            log("deadline", {"stage": "grid"})
            break
        if cap in chosen_by:
            continue
        s = step(cap)
        s["chosen_by"], s["planner_reason"] = "grid", None
        log("decision", {"cap_w": cap, "accepted": s["accepted"], "why_not": s["why_not"],
                         "energy_change_pct": s["energy_change_pct"]["mean"],
                         "p99_change_pct": s["p99_change_pct"]["mean"],
                         "quality": s["quality"]["cap_pct"], "chosen_by": "grid"})
        done.append(s)
    return done


def best_of(steps: Sequence[dict]) -> dict | None:
    ok = [s for s in steps if s["accepted"]]
    return min(ok, key=lambda s: s["energy_change_pct"]["mean"]) if ok else None


def write_manifest(out: Path) -> None:
    lines = []
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name != "MANIFEST.sha256":
            lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(out)}")
    (out / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")


def write_report(out: Path, model: str, control: dict | None, steps: Sequence[dict],
                 meta: dict) -> dict:
    best = best_of(steps)
    decisions = load_records(out)
    summary = summarize_decisions(decisions)
    res = {"label": "MEASURED", "model": model, "meta": meta, "control": control and
           {k: v for k, v in control.items() if k != "blocks"},
           "steps": [{k: v for k, v in s.items() if k != "blocks"} for s in sorted(
               steps, key=lambda s: -s["cap_w"])],
           "best_cap_w": best["cap_w"] if best else None,
           "decisions_summary": summary}
    (out / "results.json").write_text(json.dumps(res, indent=1))
    rows = ["| cap W | chosen by | J/req change (mean ± sd) | p99 change "
            "| quality pass (stock → cap) "
            "| accepted |", "|---|---|---|---|---|---|"]
    for s in res["steps"]:
        e, p, q = s["energy_change_pct"], s["p99_change_pct"], s["quality"]
        rows.append(f"| {s['cap_w']} | {s.get('chosen_by', '')} "
                    f"| {e['mean']:+.1f}% ± {e['sd']:.1f} "
                    f"| {p['mean']:+.1f}% | {q['stock_pct']:.1f}% → {q['cap_pct']:.1f}% "
                    f"| {'yes' if s['accepted'] else 'no: ' + '; '.join(s['why_not'])} |")
    if decisions:
        rows += ["", "Planner decisions:", "", format_decisions_summary(summary), "",
                 *(f"- {r['explanation']}" for r in decisions)]
    (out / "results.md").write_text("\n".join(rows) + "\n")
    write_manifest(out)
    return res


# ---------------------------------------------------------------------------------------------
# GPU host plumbing (not exercised by the unit tests)

def wait_http(url: str, timeout_s: float) -> None:  # pragma: no cover
    end = time.time() + timeout_s
    while time.time() < end:
        with contextlib.suppress(Exception):
            urllib.request.urlopen(url, timeout=5)  # noqa: S310 - localhost
            return
        time.sleep(5)
    raise TimeoutError(f"server not up: {url}")


def serve(model: str, port: int, log_path: Path):  # pragma: no cover
    argv = ["vllm", "serve", model, "--port", str(port), "--max-model-len", "4096",
            "--gpu-memory-utilization", "0.90", "--max-num-seqs", "64",
            "--served-model-name", model]
    p = subprocess.Popen(argv, stdout=log_path.open("ab"), stderr=subprocess.STDOUT)
    wait_http(f"http://127.0.0.1:{port}/v1/models", 900)
    return p


class _NoGuard:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None


def open_rig(args) -> tuple[Rig, object]:  # pragma: no cover
    wl = load_workload(args.workload or workloads_dir() / "proof-v1.jsonl", kind="proof")
    if args.fake:  # a rehearsal: fake GPU, fake model server, nothing measured
        from wattnot.proof.fake import FakeClient, FakeNvml
        nvml = FakeNvml()
        handle = nvml.device_handle(0)
        rig = Rig(nvml, handle, FakeClient(nvml, delay_s=args.fake_delay,
                                        concurrency=args.concurrency), wl,
                  concurrency=args.concurrency, repeat=args.repeat, settle_s=args.settle_s,
                  warmup=args.warmup, warmup_s=args.warmup_s, burn_in=args.burn_in,
                  counterbalance=args.counterbalance, control_blocks=args.control_blocks,
                  sleep=lambda x: time.sleep(min(x, 0.2)))
        return rig, _NoGuard()
    from wattnot.control.nvml_ctl import CapGuard
    from wattnot.probe import open_nvml
    nvml = open_nvml()
    handle = nvml.device_handle(0)
    guard = CapGuard(nvml, handle) if not args.keep_cap else _NoGuard()
    guard.__enter__()  # puts the saved limit back on exit and on SIGTERM
    client = ChatClient(f"http://127.0.0.1:{args.port}/v1", args.model)
    rig = Rig(nvml, handle, client, wl, concurrency=args.concurrency, repeat=args.repeat,
              settle_s=args.settle_s, warmup=args.warmup, warmup_s=args.warmup_s,
              burn_in=args.burn_in, counterbalance=args.counterbalance,
              control_blocks=args.control_blocks)
    return rig, guard


def run_vm_command(a, rig: Rig) -> int:  # pragma: no cover - thin dispatch, tested via vm.py
    """The events-emitting commands, driven from the control machine."""
    from wattnot.proof import vm
    from wattnot.proof.events import Stream, stdout_sink
    s = Stream(stdout_sink(a.out / "vm_events.jsonl"), label="MOCK" if a.fake else "MEASURED")
    nar = vm.Narrator(s).attach(rig)
    if a.cmd == "snap":
        vm.cmd_snap(rig, s, a.load_s)
    elif a.cmd == "baseline":
        vm.cmd_baseline(rig, s, nar, a.out)
    elif a.cmd == "control":
        r = vm.cmd_control(rig, s, nar, a.out)
        return 0 if r["bad_setting_rejected"] and r["stock_vs_stock_accepted"] else 3
    elif a.cmd == "step":
        vm.cmd_step(rig, s, nar, a.cap, a.reps, a.out, tag=a.tag, clock=a.clock)
    elif a.cmd == "apply":
        vm.cmd_apply(rig, s, a.cap)
    elif a.cmd == "adaptive":
        from wattnot.moe import adaptive_topk as at
        from wattnot.proof.adaptive import VmRig, run_adaptive
        cfg = at.AdaptiveConfig(
            model=a.adaptive_model, budget_s=a.adaptive_budget_s,
            mass=a.adaptive_mass if a.adaptive_mass is not None else at.DEFAULT_MASS)
        if a.fake:
            backend = at.FakeMoeBackend()
        else:
            from wattnot.moe.hf_backend import HFBackend
            backend = HFBackend(cfg, VmRig(rig.nvml, rig.handle, a.out))
        info = run_adaptive(s, a.out / "adaptive_topk", backend, cfg)
        print("ADAPTIVE_JSON " + json.dumps(info, default=str), flush=True)
    return 0


def prof_fields() -> tuple[str, ...]:
    from wattnot.proof.profile import FIELDS

    return FIELDS


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("cmd", choices=["full", "step", "control", "report", "snap", "baseline",
                                    "apply", "adaptive"])
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--out", type=Path, default=Path("results/proof"))
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--serve", action="store_true", help="start vLLM here (else expects it up)")
    ap.add_argument("--workload", type=Path)
    ap.add_argument("--cap", type=int, help="step: the cap to test")
    ap.add_argument("--clock", default=None,
                    help="step: SM clock profile for the cap arm (falls back to caps-only)")
    add_profile_args(ap)
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--grid", default=",".join(map(str, DEFAULT_GRID)))
    ap.add_argument("--planner", choices=["muse", "bisect"], default="muse")
    ap.add_argument("--max-proposals", type=int, default=4)
    ap.add_argument("--budget-s", type=float, default=0, help="stop starting new caps after this")
    ap.add_argument("--tag", default="")
    ap.add_argument("--emit", action="store_true", help="step/control: emit EVT event lines")
    ap.add_argument("--fake", action="store_true", help="rehearsal: fake GPU, MOCK labels")
    ap.add_argument("--fake-delay", type=float, default=0.0, help="seconds per fake request")
    ap.add_argument("--settle-s", type=float, default=4.0)
    ap.add_argument("--keep-cap", action="store_true", help="apply: leave the cap set on exit")
    ap.add_argument("--load-s", type=float, default=0, help="snap: seconds of load at stock cap")
    ap.add_argument("--adaptive-model", default="ibm-granite/granite-3.1-3b-a800m-instruct")
    ap.add_argument("--adaptive-mass", type=float, default=None,
                    help="adaptive top-k: share of router weight to keep (default: the tuned one)")
    ap.add_argument("--adaptive-budget-s", type=float, default=1200)
    a = ap.parse_args(argv)
    prof = resolve_profile(a.profile, **{k: getattr(a, k) for k in prof_fields()})
    for k, v in prof.as_dict().items():  # the resolved values, flag by flag
        setattr(a, k, v)
    a.out.mkdir(parents=True, exist_ok=True)
    log = make_log(a.out)
    if a.cmd == "report":
        steps = [json.loads(p.read_text()) for p in sorted((a.out / "steps").glob("*.json"))]
        cpath = a.out / "control.json"
        ctl = json.loads(cpath.read_text()) if cpath.exists() else None
        write_report(a.out, a.model, ctl, steps, {"note": "re-generated"})
        return 0
    if a.cmd == "apply":
        a.keep_cap = True
    proc = serve(a.model, a.port, a.out / "vllm.log") if a.serve else None
    rig, guard = open_rig(a)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(143)))
    t_start = time.time()
    try:
        if a.cmd in ("snap", "baseline", "apply", "adaptive") or (
                a.cmd in ("control", "step") and a.emit):
            return run_vm_command(a, rig)
        if a.cmd == "control":
            r = run_control(rig, a.out, workload=rig.workload, log=log)
            print(json.dumps({k: v for k, v in r.items() if k != "blocks"}))
            return 0 if r["bad_setting_rejected"] and r["stock_vs_stock_accepted"] else 3
        if a.cmd == "step":
            s = run_step(rig, a.cap, a.reps, a.out, workload=rig.workload, log=log, tag=a.tag,
                         clock=a.clock)
            print("STEP_JSON " + json.dumps({k: v for k, v in s.items() if k != "blocks"}))
            return 0
        control = run_control(rig, a.out, workload=rig.workload, log=log)
        if not control["bad_setting_rejected"]:
            log("abort", {"why": "the gate did not reject the deliberately bad setting"})
            return 3
        planner = BisectPlanner()
        if a.planner == "muse":
            from wattnot.meta.client import GuardedClient
            from wattnot.proof.planner import MusePlanner
            gc = GuardedClient()
            if gc.key:
                planner = MusePlanner(gc)
            else:
                log("planner", {"note": "no Meta key on this host; using the bisect rule"})
        steps = run_session(
            lambda cap: run_step(rig, cap, a.reps, a.out, workload=rig.workload, log=log,
                                 tag=a.tag),
            planner, [int(x) for x in a.grid.split(",") if x], log,
            max_proposals=a.max_proposals,
            deadline=(t_start + a.budget_s) if a.budget_s else None, out=a.out)
        write_report(a.out, a.model, control, steps,
                     {"started": datetime.fromtimestamp(t_start, UTC).isoformat(
                         timespec="seconds"),
                      "finished": now(), "planner": getattr(planner, "name", "?"),
                      "reps": a.reps, "repeat": a.repeat, "concurrency": a.concurrency,
                      "default_w": rig.default_w, "gpu_min_w": rig.min_w, "gpu_max_w": rig.max_w})
        return 0
    finally:
        if not a.keep_cap:
            with contextlib.suppress(Exception):
                rig.set_cap(rig.default_w)
        guard.__exit__(None, None, None)
        if proc is not None:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
