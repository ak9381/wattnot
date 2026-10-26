"""Write the EXAMPLE run that drives the run screen (web/run/demo-events.jsonl).

Fake backend: an in-memory GPU and a toy latency model, so every GPU number
is an EXAMPLE. Usage: python scripts/demo_run.py [OUT_DIR] [--live]

Default: the fixed fallback policy picks each cap, with no network call.
--live: Muse Spark (muse-spark-1.3, minimal effort, 600-token cap) picks each
step through the planned loop, with the usual clamp and fallback. Only the
numeric summary is sent. Needs META_API_KEY. Writes demo-events.live.jsonl,
so the screen shows real model ids and token counts next to EXAMPLE GPU data.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wattnot.control import CapGuard, step_down  # noqa: E402
from wattnot.control.planned import Measured, planned_loop  # noqa: E402
from wattnot.events.emitter import Emitter  # noqa: E402
from wattnot.planner import DecisionMaker, SparkClient  # noqa: E402

RUN = "run_20261027_090000_e0a3b1"
RUN_LIVE = "run_20261027_091500_5a7c2e"
BASE_P99, BASE_WH = 1.000, 0.0480
PROMPTS = ["Summarise this 2k-token support ticket", "Write a SQL query for monthly churn",
           "Explain this Python traceback", "Translate a product FAQ to Spanish"]


class DemoGPU:
    """In-memory power limit, 200-700 W, starts at 700 W."""

    def __init__(self):
        self.mw = 700_000

    def power_limit_constraints_mw(self, h):
        return 200_000, 700_000

    def power_limit_mw(self, h):
        return self.mw

    def set_power_limit_mw(self, h, mw):
        self.mw = mw


def toy(cap):  # p99 climbs and Wh/request falls as the cap drops
    p99 = BASE_P99 * (1 + (700 - cap) / 2000 + (0.15 if cap < 550 else 0))
    wh = BASE_WH * (0.62 + 0.38 * cap / 700) if cap >= 500 else BASE_WH * 0.80
    return round(p99, 3), round(wh, 5)


LIVE_STEPS = 6  # at most 6 Muse Spark calls per live run


def main(out: Path, live: bool = False) -> Path:
    if live:
        return main_live(out)
    tmp = out / "_tmp"
    with Emitter(RUN, root=tmp) as em, CapGuard(DemoGPU(), 0, handle_sigterm=False) as g:
        em.emit("run.started", {"mode": "demo", "label": "EXAMPLE",
                                "host": {"provider": "fake", "gpu": "H100 (fake)"},
                                "model": "meta-llama/Llama-3.1-8B-Instruct",
                                "workload": "demo-prompts-v1"})
        em.emit("telemetry.sample", {"gpu_index": 0, "power_w": 612, "source": "estimate",
                                     "label": "EXAMPLE"})
        em.emit("step.started", {"kind": "baseline", "label": "Measure baseline"},
                step_id="baseline")
        em.emit("trial.measured", {"trial_no": 0, "cap_w": 700, "wh_per_req": {"mean": BASE_WH},
                                   "p99_ms": BASE_P99 * 1000, "label": "MEASURED"},
                step_id="baseline")
        n = {"i": 0, "last_cap": 700}

        def trial(cap):
            n["i"] += 1
            i, s = n["i"], f"cap:{cap}"
            em.emit("decision", {"choice": {"kind": "cap", "power_cap_w": cap, "knob": None,
                                            "value": None},
                                 "reason": f"p99 has headroom at {n['last_cap']} W; "
                                           f"try {cap} W",
                                 "model": "fallback", "tokens": {"input": 0, "output": 0},
                                 "clamped": False}, step_id=s)
            em.emit("step.started", {"kind": "apply", "label": f"Set cap {cap} W",
                                     "power_cap_w": cap}, step_id=s)
            p99, wh = toy(cap)
            em.emit("telemetry.sample", {"gpu_index": 0, "power_w": round(cap * 0.88),
                                         "source": "estimate", "label": "EXAMPLE"}, step_id=s)
            em.emit("step.started", {"kind": "prompts", "prompts": PROMPTS}, step_id=s)
            em.emit("trial.measured", {"trial_no": i, "cap_w": cap, "wh_per_req": {"mean": wh},
                                       "p99_ms": round(p99 * 1000), "label": "MEASURED"},
                    step_id=s)
            return SimpleNamespace(cap=cap, p99=p99, wh=wh, step=s, i=i)

        def gate(r):
            ok = r.p99 <= BASE_P99 * 1.10
            em.emit("gate.result", {"trial_no": r.i, "check": "p99", "passed": ok,
                                    "detail": {"score": round(r.p99 * 1000),
                                               "threshold": round(BASE_P99 * 1100)}},
                    step_id=r.step)
            em.emit("gate.result", {"trial_no": r.i, "check": "fixed", "passed": True,
                                    "detail": {"score": 0.4, "threshold": 1.0}}, step_id=r.step)
            if ok:
                n["last_cap"] = r.cap
            return SimpleNamespace(passed=ok, failed=[] if ok else ["p99"])

        step_down(g, start_w=700, floor_w=400, step_w=50, trial=trial, gate=gate, emit=em,
                  baseline=(BASE_WH, BASE_P99), metrics=lambda r: (r.wh, r.p99),
                  rec_opts={"requests_per_gpu_per_day": 100_000, "gpus": 8})
        # comparison: a plain fixed cap at the driver floor, no gate (EXAMPLE)
        p99, wh = toy(500)
        em.emit("pareto.updated", {"label": "EXAMPLE", "compare": {
            "name": "plain fixed cap 500 W, no gate", "cap_w": 500, "wh_per_req": wh,
            "p99_ms": round(p99 * 1000), "gate_passed": p99 <= BASE_P99 * 1.10}})
        em.emit("run.finished", {"status": "ok", "label": "EXAMPLE"})
    src = tmp / "runs" / RUN / "events.jsonl"
    dst = out / "demo-events.jsonl"
    shutil.copy(src, dst)
    shutil.rmtree(tmp)
    return dst


def _start(em):
    em.emit("run.started", {"mode": "demo", "label": "EXAMPLE",
                            "host": {"provider": "fake", "gpu": "H100 (fake)"},
                            "model": "meta-llama/Llama-3.1-8B-Instruct",
                            "workload": "demo-prompts-v1"})
    em.emit("telemetry.sample", {"gpu_index": 0, "power_w": 612, "source": "estimate",
                                 "label": "EXAMPLE"})
    em.emit("step.started", {"kind": "baseline", "label": "Measure baseline"},
            step_id="baseline")
    em.emit("trial.measured", {"trial_no": 0, "cap_w": 700, "wh_per_req": {"mean": BASE_WH},
                               "p99_ms": BASE_P99 * 1000, "label": "MEASURED"},
            step_id="baseline")


def main_live(out: Path, client: SparkClient | None = None) -> Path:
    """Same fake GPU, but Muse Spark makes each decision (real calls)."""

    client = client or SparkClient(max_tokens=600)
    tmp = out / "_tmp_live"
    with Emitter(RUN_LIVE, root=tmp) as em, \
            CapGuard(DemoGPU(), 0, handle_sigterm=False) as g:
        _start(em)
        n = {"i": 0}

        def trial(cap, knobs):
            n["i"] += 1
            s = f"plan:{n['i'] - 1}"
            em.emit("step.started", {"kind": "apply", "label": f"Set cap {cap} W",
                                     "power_cap_w": cap}, step_id=s)
            p99, wh = toy(cap)
            em.emit("telemetry.sample", {"gpu_index": 0, "power_w": round(cap * 0.88),
                                         "source": "estimate", "label": "EXAMPLE"}, step_id=s)
            em.emit("step.started", {"kind": "prompts", "prompts": PROMPTS}, step_id=s)
            em.emit("trial.measured", {"trial_no": n["i"], "cap_w": cap,
                                       "wh_per_req": {"mean": wh},
                                       "p99_ms": round(p99 * 1000), "label": "MEASURED"},
                    step_id=s)
            return Measured(p99_s=p99, wh_per_req=wh)

        def gate(m):
            ok = m.p99_s <= BASE_P99 * 1.10
            em.emit("gate.result", {"trial_no": n["i"], "check": "p99", "passed": ok,
                                    "detail": {"score": round(m.p99_s * 1000),
                                               "threshold": round(BASE_P99 * 1100)}},
                    step_id=f"plan:{n['i'] - 1}")
            return SimpleNamespace(passed=ok, failed=[] if ok else ["p99"])

        planned_loop(g, DecisionMaker(client, emit=em, client=client),
                     baseline=Measured(BASE_P99, BASE_WH), trial=trial, gate=gate,
                     knobs={"enable_prefix_caching": True}, step_w=50,
                     max_steps=LIVE_STEPS, emit=em,
                     rec_opts={"requests_per_gpu_per_day": 100_000, "gpus": 8})
        p99, wh = toy(500)
        em.emit("pareto.updated", {"label": "EXAMPLE", "compare": {
            "name": "plain fixed cap 500 W, no gate", "cap_w": 500, "wh_per_req": wh,
            "p99_ms": round(p99 * 1000), "gate_passed": p99 <= BASE_P99 * 1.10}})
        em.emit("run.finished", {"status": "ok", "label": "EXAMPLE"})
    dst = out / "demo-events.live.jsonl"
    shutil.copy(tmp / "runs" / (RUN_LIVE) / "events.jsonl", dst)
    shutil.rmtree(tmp)
    return dst


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--live"]
    print(main(Path(args[0]) if args else ROOT / "web" / "run", live="--live" in sys.argv))
