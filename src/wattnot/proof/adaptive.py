"""Adaptive top-k as a step of the proof run, narrated as events.

A Mixture-of-Experts model sends each token to its top-k experts. Adaptive top-k keeps only the
experts that cover 95% of the router weight (the default `mass`), so some tokens use fewer.
Llama is dense and has no experts, so this step loads a small MoE model (Granite 3.1 3B-A800M)
after the Llama stages. It is time-boxed, and when it cannot run it says so in one event and the
rest of the run goes on.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import time
from pathlib import Path

from wattnot.moe import adaptive_topk as at
from wattnot.proof.events import Stream

STEP = "adaptive_topk"


class VmRig:
    """What `HFBackend` asks of a rig: GPU name, driver, a way to free the GPU, a meter."""

    def __init__(self, nvml, handle, out: Path) -> None:
        self.nvml, self.handle, self.out = nvml, handle, out
        self.gpu = nvml.device_name(handle)
        self.driver = nvml.driver_version()
        self.provider = "e2e"

    def release_gpu(self) -> None:
        """Stop the vLLM server from the Llama stages so the GPU memory is free."""
        with contextlib.suppress(Exception):
            subprocess.run(["pkill", "-f", "vllm serve"], check=False, timeout=30)
        time.sleep(10)
        # the engine core is a child process and can outlive the API server: make sure it let go
        with contextlib.suppress(Exception):
            subprocess.run(["pkill", "-9", "-f", "VLLM::"], check=False, timeout=30)
        for _ in range(30):
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    check=True, capture_output=True, text=True, timeout=15,
                ).stdout
                if int(out.split()[0]) < 2048:  # MiB: the card is free again
                    return
            except Exception:  # noqa: BLE001 - no reading: fall back to a fixed wait
                time.sleep(10)
                return
            time.sleep(2)

    def meter(self, params):
        from wattnot.meter.nvml import NvmlMeter

        return NvmlMeter(
            self.nvml, run_id=f"adaptive-{int(time.time())}", traces_root=self.out / "traces"
        )


class _Evented:
    """Wraps a backend: same behaviour, plus one event per phase and per metered window."""

    def __init__(self, inner, s: Stream, cfg: at.AdaptiveConfig) -> None:
        self.inner, self.s, self.cfg = inner, s, cfg
        self.simulated, self.gpu, self.driver = inner.simulated, inner.gpu, inner.driver
        self._arm = "off"
        self._rep: dict[str, int] = {"off": 0, "on": 0}
        self.agreement_result: dict = {}
        self.prepare_result: dict = {}

    def prepare(self, deadline, clock):
        with self.s.step(
            f"{STEP}.load",
            "adaptive",
            "load",
            f"Load {self.cfg.model} and check the hook",
            parent=STEP,
            inputs="stops vLLM, downloads the model if needed, loads it in bf16",
        ) as st:
            self.prepare_result = self.inner.prepare(deadline, clock)
            p = self.prepare_result
            st.outputs = f"router top-k = {p.get('top_k')}" + (
                f", {p['layers']} MoE layers" if p.get("layers") else ""
            )
            st.add("top-k (normal)", p.get("top_k"), "experts")
            st.add("MoE layers", p.get("layers"), "")
        with self.s.step(
            f"{STEP}.hook",
            "adaptive",
            "hook",
            "Check the hooked model matches the stock one",
            parent=STEP,
            inputs="one prompt, argmax of the next token, stock vs hooked 'off'",
        ) as st:
            ok = bool(self.prepare_result.get("sanity", {}).get("hooked_off_matches_stock", True))
            st.ok = ok
            st.outputs = "same next token" if ok else "MISMATCH"
        return self.prepare_result

    def set_arm(self, arm):
        self._arm = arm
        self.inner.set_arm(arm)

    def generate(self, prompts):
        return self.inner.generate(prompts)

    def reset_counters(self):
        self.inner.reset_counters()

    def active_experts(self):
        return self.inner.active_experts()

    def agreement(self):
        with self.s.step(
            f"{STEP}.agree",
            "adaptive",
            "agree",
            "Does adaptive top-k pick the same next token as normal top-k?",
            parent=STEP,
            inputs=f"{self.cfg.agree_prompts} prompts × {self.cfg.agree_new_tokens} "
            "tokens, teacher-forced on the normal arm's own text",
        ) as st:
            r = self.inner.agreement()
            self.agreement_result = r
            ok = r["pct"] >= self.cfg.min_agreement_pct
            st.outputs = f"{r['pct']:.2f}% of {r['positions']} positions agree"
            st.add("agreement", r["pct"], "%")
            st.add("needs at least", self.cfg.min_agreement_pct, "%", "ADVICE")
            st.extra = {"passed": ok}
        self.s.emit(
            "gate.result",
            {
                "trial_no": 0,
                "check": "adaptive_agreement",
                "passed": ok,
                "detail": {
                    "score": round(r["pct"], 2),
                    "threshold": self.cfg.min_agreement_pct,
                    "unit": "% token agreement",
                },
                "label": self.s.label,
            },
            step_id=f"{STEP}.agree",
            parent=STEP,
        )
        return r

    def energy_meter(self):
        return _EventedMeter(self.inner.energy_meter(), self)

    def close(self):
        self.inner.close()


class _EventedMeter:
    def __init__(self, inner, owner: _Evented) -> None:
        self.inner, self.o = inner, owner
        self.st = None

    def start(self, label: str) -> None:
        o = self.o
        n = o._rep[o._arm]
        self.st = o.s.start(
            f"{STEP}.arms.{o._arm}{n}",
            "adaptive",
            "arms",
            f"Metered run: adaptive top-k {o._arm.upper()}, repeat {n + 1}",
            parent=STEP,
            inputs=f"{o.cfg.n_prompts} prompts, batches of "
            f"{o.cfg.batch}, {o.cfg.max_new_tokens} new tokens each",
        )
        self.inner.start(label)

    def stop(self):
        w = self.inner.stop()
        o, st = self.o, self.st
        n = o._rep[o._arm]
        o._rep[o._arm] += 1
        j_req = w.wh * 3600 / o.cfg.n_prompts
        k_eff = o.inner.active_experts()
        st.outputs = f"{w.wh * 3600:.0f} J in {w.seconds:.1f} s"
        st.tag = w.label if w.label in ("MEASURED", "ESTIMATE") else o.s.label
        st.add("energy per request", j_req, "J/req")
        st.add("mean power", w.mean_w, "W")
        st.add("experts used per token", k_eff, "k")
        o.s.finish(st)
        o.s.emit(
            "trial.measured",
            {
                "trial_no": o._rep["off"] + o._rep["on"],
                "arm": o._arm,
                "rep": n,
                "wh_per_req": {"mean": round(w.wh / o.cfg.n_prompts, 6)},
                "j_per_req": round(j_req, 3),
                "active_experts": k_eff,
                "mean_w": round(w.mean_w, 1),
                "label": st.tag,
            },
            step_id=f"{STEP}.arms.{o._arm}{n}",
            parent=STEP,
        )
        return w


def run_adaptive(s: Stream, out: Path, backend, cfg: at.AdaptiveConfig) -> dict:
    """The whole step. Returns the summary (also written to out/adaptive_topk/summary.json)."""
    out.mkdir(parents=True, exist_ok=True)
    top = s.start(
        STEP,
        "adaptive",
        "arms",
        "Adaptive top-k: fewer experts per token, same answers?",
        inputs=f"{cfg.model}, keep experts covering {cfg.mass:.0%} of router weight; "
        f"time box {cfg.budget_s:.0f} s",
        top=STEP,
    )
    wrapped = _Evented(backend, s, cfg)
    try:
        res = at.run_trial(wrapped, cfg)
        summ = at.summarize(res)
    except Exception as e:  # noqa: BLE001 - the step may fail; the run goes on and says so
        top.ok, top.outputs = False, f"skipped: {type(e).__name__}: {str(e)[:200]}"
        top.summary = "Adaptive top-k did not run: " + top.outputs
        s.emit("error", {**top.base, "message": top.outputs}, step_id=STEP, parent=None)
        s.finish(top)
        info = {"ran": False, "reason": top.outputs}
        (out / "summary.json").write_text(json.dumps(info, indent=1))
        return info
    w = at.write_receipt(res, out)
    j_off = res_j_per_req(res, "off")
    j_on = res_j_per_req(res, "on")
    lab = summ["label"] if summ["label"] in ("MEASURED", "ESTIMATE") else s.label
    for name, g in summ["gates"].items():
        s.emit(
            "gate.result",
            {
                "trial_no": 0,
                "check": f"adaptive_{name}",
                "passed": g["passed"],
                "detail": {k: v for k, v in g.items() if k != "passed"},
                "label": lab,
            },
            step_id=STEP,
            parent=None,
        )
    top.tag = lab
    top.outputs = (
        f"agreement {res.agreement['pct']:.1f}%, J/req {j_off:.2f} → {j_on:.2f} "
        f"({-summ['saving_pct']:+.1f}% J/token), p99 {summ['p99_increase_pct']:+.1f}%"
    )
    top.summary = top.outputs
    top.add("top-k (normal)", res.top_k, "experts")
    top.add("experts used with adaptive", summ["on"]["active_experts"], "experts")
    top.add("agreement", res.agreement["pct"], "%")
    top.add("energy per request, normal", j_off, "J/req")
    top.add("energy per request, adaptive", j_on, "J/req")
    top.add("energy per token change", -summ["saving_pct"], "%")
    top.add("p99 change", summ["p99_increase_pct"], "%")
    top.extra = {"all_gates_passed": summ["all_gates_passed"], "truncated": res.truncated}
    s.finish(top)
    info = {
        "ran": True,
        "label": lab,
        "model": cfg.model,
        "mass": cfg.mass,
        "top_k": res.top_k,
        "active_experts_on": summ["on"]["active_experts"],
        "agreement_pct": res.agreement["pct"],
        "j_per_req_off": j_off,
        "j_per_req_on": j_on,
        "j_per_token_change_pct": -summ["saving_pct"],
        "p99_change_pct": summ["p99_increase_pct"],
        "gates": summ["gates"],
        "all_gates_passed": summ["all_gates_passed"],
        "truncated": res.truncated,
        "reps": {"off": summ["off"]["reps"], "on": summ["on"]["reps"]},
        "receipt": str(Path(w["path"]).name),
        "receipt_id": w["receipt"]["receipt_id"],
        "seconds": round(res.seconds, 1),
    }
    (out / "summary.json").write_text(json.dumps(info, indent=1))
    return info


def res_j_per_req(res: at.TrialResult, arm: str) -> float:
    runs = [r for r in res.runs if r.arm == arm]
    return sum(r.wh * 3600 / r.requests for r in runs) / len(runs) if runs else float("nan")
