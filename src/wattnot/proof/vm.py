"""The GPU-host side of the run: each command measures something and emits its own step events.

`Narrator` listens to the rig (phases, samples) and to the session code's `log` calls and turns
them into `wattnot.event/1` sub-steps, so what the dashboard shows is what the rig really did, in
the order it did it, with its real timings:

    settle  -> apply.cap         warmup  -> workload.send       metered -> workload.time
    metered end -> telemetry.integrate (the energy counter delta)    grade -> gates.fixed
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from wattnot.proof.events import Stream, num
from wattnot.proof.rig import Rig
from wattnot.proof.session import run_control, run_step, save_raw


class Narrator:
    """Rig hooks + `log` callable that write sub-step events under the current block."""

    def __init__(self, s: Stream, *, sample_every_s: float = 2.0) -> None:
        self.s, self.every = s, sample_every_s
        self.block_id: str | None = None
        self.parent: str | None = None
        self.trial_no = 0
        self._open: dict[str, object] = {}
        self._last_sample = 0.0
        self._lock = threading.Lock()

    def attach(self, rig: Rig) -> Narrator:
        rig.on_phase, rig.on_sample = self.phase, self.sample
        return self

    def at(self, block_id: str, parent: str) -> None:
        self.block_id, self.parent = block_id, parent

    # ---- rig hooks
    def sample(self, d: dict) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._last_sample < self.every:
                return
            self._last_sample = now
        self.s.telemetry(
            power_w=round(d["power_w"], 1),
            cap_w=d.get("cap_w"),
            temp_c=d.get("temp_c"),
            sm_mhz=d.get("sm_mhz"),
            label=self.s.label,
        )

    def phase(self, name: str, state: str, info: dict) -> None:
        if not self.block_id:
            return
        bid, parent = self.block_id, self.parent
        node, sub = {
            "settle": ("apply", "cap"),
            "warmup": ("workload", "send"),
            "metered": ("workload", "time"),
            "grade": ("gates", "fixed"),
        }[name]
        if state == "start":
            label = {
                "settle": f"Set the cap to {info.get('asked_w')} W",
                "warmup": f"Warm-up: {info.get('n')} requests, not counted",
                "metered": f"Timed pass: {info.get('requests')} requests, "
                f"{info.get('concurrency')} at a time",
                "grade": f"Check {info.get('prompts')} prompts against their gold answers",
            }[name]
            self._open[name] = self.s.start(
                f"{bid}.{name}", node, sub, label, parent=parent, inputs=label
            )
            return
        st = self._open.pop(name)
        if name == "settle":
            st.outputs = f"driver read back {info['readback_w']} W"
            st.add("cap read back", info["readback_w"], "W")
        elif name == "warmup":
            st.outputs = f"{info['n']} requests sent"
        elif name == "metered":
            st.outputs = (
                f"{info['requests']} requests in {info['wall_s']:.1f} s, "
                f"{info['joules']:.0f} J on the GPU's energy counter"
            )
            st.add("p50", info["p50_s"], "s")
            st.add("p99", info["p99_s"], "s")
            st.add("mean power", info["mean_w"], "W")
            st.add("tokens", info["tokens"], "")
        elif name == "grade":
            st.outputs = (
                f"{info['pass_pct']:.1f}% of {info['checked']} graded answers pass"
                f" ({info['truncated']} cut short)"
            )
            st.add("answers pass", info["pass_pct"], "%")
        self.s.finish(st)
        if name == "metered":
            integ = self.s.start(
                f"{bid}.integrate",
                "telemetry",
                "integrate",
                "Energy = counter after minus counter before",
                parent=parent,
                inputs="NVML total_energy_mj, read before and after the pass",
            )
            integ.outputs = f"{info['joules']:.0f} J over {info['wall_s']:.1f} s"
            integ.add("energy", info["joules"], "J")
            integ.add("per request", info["joules"] / max(1, info["requests"]), "J/req")
            self.s.finish(integ)

    # ---- the session code's `log`
    def log(self, kind: str, d: dict) -> None:
        if kind == "block_start":
            if "name" in d:
                self.at(f"{self.parent_id}.{d['name']}", self.parent_id)
            else:
                tag = f"{'s' if d['arm'] == 'stock' else 'c'}{d['rep']}"
                self.at(f"{self.parent_id}.{tag}", self.parent_id)
                if d["arm"] == "cap" and d["rep"] == 0:
                    self.s.emit(
                        "action.applied",
                        {"change": {"power_cap_w": d["cap_w"]}, "label": self.s.label},
                        step_id=self.parent_id,
                        parent=self.top,
                    )
        elif kind == "block":
            self.trial_no += 1
            self.s.emit(
                "trial.measured",
                {
                    "trial_no": self.trial_no,
                    "cap_w": d["set_w"],
                    "arm": d["arm"],
                    "rep": d["rep"],
                    "wh_per_req": {"mean": round(d["j_per_req"] / 3600, 5)},
                    "j_per_req": round(d["j_per_req"], 3),
                    "p99_ms": round(d["p99_s"] * 1000, 1),
                    "pass_rate": round(d["pass_pct"] / 100, 4),
                    "mean_w": d.get("mean_w"),
                    "temp_c": d.get("temp_c"),
                    "label": self.s.label,
                },
                step_id=self.parent_id,
                parent=self.top,
            )

    parent_id = ""
    top = "cap_sweep"


# ---------------------------------------------------------------------------------------------


def cmd_snap(rig: Rig, s: Stream, load_s: float, parent: str | None = "room_scan") -> dict:
    """A live NVML reading of the GPU: idle, then (optionally) under stock-cap load."""
    from wattnot.probe import CLOCK_SM

    nv, h = rig.nvml, rig.handle
    with s.step(
        "room_scan.nvml",
        "telemetry",
        "probe",
        "Read the H100 live from NVML",
        parent=parent,
        inputs="power, temperature, clock and cap limits, from the driver",
    ) as st:
        idle = {
            "name": nv.device_name(h),
            "power_w": nv.power_usage_mw(h) / 1000.0,
            "temp_c": int(nv.temperature_c(h)),
            "sm_mhz": int(nv.clock_mhz(h, CLOCK_SM)),
            "cap_w": int(nv.power_limit_mw(h)) // 1000,
            "default_w": rig.default_w,
            "min_w": rig.min_w,
            "max_w": rig.max_w,
        }
        st.outputs = (
            f"{idle['name']}: {idle['power_w']:.0f} W, {idle['temp_c']} °C, cap {idle['cap_w']} W"
        )
        st.add("power now", idle["power_w"], "W")
        st.add("GPU temperature", idle["temp_c"], "°C")
        st.add("cap", idle["cap_w"], "W")
    loaded = None
    if load_s > 0:
        with s.step(
            "room_scan.load",
            "workload",
            "send",
            f"Short load at the stock cap ({load_s:.0f} s) to read the rack's real draw",
            parent=parent,
            inputs="the proof prompts, in a loop",
        ) as st:
            rig.set_cap(rig.default_w)
            items = list(rig.workload)
            stop = threading.Event()
            temps: list[int] = []
            done = [0]

            def worker(k: int) -> None:
                i = k
                while not stop.is_set():
                    it = items[i % len(items)]
                    rig.client.ask(it.prompt, it.max_tokens)
                    done[0] += 1
                    i += rig.concurrency

            ths = [
                threading.Thread(target=worker, args=(k,), daemon=True)
                for k in range(rig.concurrency)
            ]
            e0, t0 = int(nv.total_energy_mj(h)), time.perf_counter()
            for t in ths:
                t.start()
            end = time.monotonic() + load_s
            while time.monotonic() < end:
                temps.append(int(nv.temperature_c(h)))
                s.telemetry(
                    power_w=round(nv.power_usage_mw(h) / 1000.0, 1),
                    cap_w=rig.default_w,
                    temp_c=temps[-1],
                    label=s.label,
                )
                time.sleep(min(2.0, max(0.05, end - time.monotonic())))
            stop.set()
            e1, t1 = int(nv.total_energy_mj(h)), time.perf_counter()
            for t in ths:
                t.join(timeout=60)
            loaded = {
                "power_w": (e1 - e0) / 1000.0 / (t1 - t0),
                "temp_c": max(temps or [0]),
                "cap_w": rig.default_w,
                "requests": done[0],
            }
            st.outputs = (
                f"{loaded['power_w']:.0f} W mean over {t1 - t0:.0f} s, "
                f"hottest {loaded['temp_c']} °C"
            )
            st.add("mean power (energy counter)", loaded["power_w"], "W")
            st.add("hottest GPU reading", loaded["temp_c"], "°C")
    snap = {"idle": idle, "loaded": loaded, "label": s.label}
    print("SNAP_JSON " + json.dumps(snap), flush=True)
    return snap


def cmd_baseline(rig: Rig, s: Stream, nar: Narrator, out: Path) -> dict:
    n = len(rig.workload)
    with s.step(
        "baseline",
        "workload",
        "send",
        f"Baseline: {n} prompts at the stock {rig.default_w} W cap",
        inputs=f"{n} prompts × {rig.repeat}, {rig.concurrency} at a time, cap {rig.default_w} W",
        top="baseline",
    ) as st:
        nar.parent_id, nar.top = "baseline", "baseline"
        nar.at("baseline.b0", "baseline")
        b = rig.block(rig.default_w)
        nar.log(
            "block",
            {
                "set_w": rig.default_w,
                "arm": "stock",
                "rep": 0,
                **{
                    "j_per_req": b["j_per_req"],
                    "p99_s": b["p99_s"],
                    "pass_pct": b["pass_pct"],
                    "mean_w": b["counter_mean_w"],
                    "temp_c": b["max_temp_c"],
                },
            },
        )
        slim = save_raw(out, "baseline-stock", b)
        (out / "baseline.json").write_text(json.dumps(slim, indent=1))
        st.outputs = (
            f"{b['pass_pct']:.1f}% of {b['checked']} answers pass; "
            f"{b['j_per_req']:.1f} J/request; p99 {b['p99_s']:.2f} s"
        )
        st.add("answers pass", b["pass_pct"], "%")
        st.add("energy per request", b["j_per_req"], "J/req")
        st.add("p99 latency", b["p99_s"], "s")
        st.add("mean power", b["counter_mean_w"], "W")
        st.add("hottest GPU reading", b["max_temp_c"], "°C")
    return slim


def cmd_control(rig: Rig, s: Stream, nar: Narrator, out: Path) -> dict:
    with s.step(
        "quality_gate",
        "gates",
        "decide",
        "Quality gate self-check: does it catch a deliberately bad setting?",
        inputs="stock, then answers cut to 20% of their token budget, then stock again",
        top="quality_gate",
    ) as st:
        nar.parent_id, nar.top = "quality_gate", "quality_gate"
        res = run_control(rig, out, workload=rig.workload, log=nar.log)
        for name, passed, detail in (
            (
                "bad_setting_rejected",
                res["bad_setting_rejected"],
                {
                    "score": round(res["truncated_pass_pct"], 1),
                    "threshold": round(res["stock_pass_pct"] - 2.0, 1),
                    "note": "pass rate of the cut-short answers vs stock minus 2 points",
                },
            ),
            (
                "stock_vs_stock_accepted",
                res["stock_vs_stock_accepted"],
                {
                    "score": round(res["stock2_pass_pct"], 1),
                    "threshold": round(res["stock_pass_pct"] - 2.0, 1),
                    "note": "an unchanged setting is not rejected",
                },
            ),
        ):
            s.emit(
                "gate.result",
                {
                    "trial_no": 0,
                    "check": name,
                    "passed": bool(passed),
                    "detail": detail,
                    "label": s.label,
                },
                step_id="quality_gate",
                parent=None,
            )
        ok = res["bad_setting_rejected"] and res["stock_vs_stock_accepted"]
        st.ok = ok
        st.outputs = (
            f"bad setting: {res['truncated_pass_pct']:.1f}% pass vs "
            f"{res['stock_pass_pct']:.1f}% at stock → "
            f"{'rejected' if res['bad_setting_rejected'] else 'NOT rejected'}; "
            f"stock vs stock → {'accepted' if res['stock_vs_stock_accepted'] else 'rejected'}"
        )
        st.add("cut-short answers pass", res["truncated_pass_pct"], "%")
        st.add("stock answers pass", res["stock_pass_pct"], "%")
        st.add("stock vs stock energy drift", res["stock_vs_stock_j_per_req_change_pct"], "%")
    return res


def cmd_step(
    rig: Rig, s: Stream, nar: Narrator, cap: int, reps: int, out: Path, tag: str = "",
    clock: str | None = None,
) -> dict:
    ck = clock if clock and clock != "base" else None
    sid = f"cap_sweep.cap{cap}" + (f".{ck}" if ck else "")
    with s.step(
        sid,
        "e2e_rig",
        "sweep",
        f"Test a {cap} W cap against stock",
        inputs=f"{reps} paired rounds: stock block, then {cap} W block, same prompts",
        parent="cap_sweep",
        top="cap_sweep",
    ) as st:
        nar.parent_id, nar.top = sid, "cap_sweep"
        summ = run_step(rig, cap, reps, out, workload=rig.workload, log=nar.log,
                        tag=tag + (f"{ck}-" if ck else ""), clock=ck)
        if summ.get("clock_fallback"):
            s.emit("step.progress", {"node": "e2e_rig", "sub": "sweep", "pct": 50,
                                     "message": f"clock lock refused, {summ['clock_fallback']}"},
                   step_id=sid, parent="cap_sweep")
        e, p, q = summ["energy_change_pct"], summ["p99_change_pct"], summ["quality"]
        s.emit(
            "gate.result",
            {
                "trial_no": cap,
                "check": "quality",
                "passed": q["passed"],
                "detail": {
                    "score": round(q["cap_pct"], 1),
                    "threshold": round(q["stock_pct"] - 2.0, 1),
                },
                "label": s.label,
            },
            step_id=sid,
            parent="cap_sweep",
        )
        s.emit(
            "gate.result",
            {
                "trial_no": cap,
                "check": "p99",
                "passed": p["mean"] <= 10.0,
                "detail": {"score": round(p["mean"], 2), "threshold": 10.0, "unit": "% change"},
                "label": s.label,
            },
            step_id=sid,
            parent="cap_sweep",
        )
        s.emit(
            "gate.result",
            {
                "trial_no": cap,
                "check": "energy",
                "passed": e["mean"] + e["ci95_half"] < 0,
                "detail": {
                    "score": round(e["mean"] + e["ci95_half"], 2),
                    "threshold": 0.0,
                    "unit": "% change, upper 95% bound",
                },
                "label": s.label,
            },
            step_id=sid,
            parent="cap_sweep",
        )
        if not summ["accepted"]:
            s.emit(
                "action.rolled_back",
                {
                    "change": {"power_cap_w": cap},
                    "restored_cap_w": rig.default_w,
                    "failed_gates": summ["why_not"],
                    "label": s.label,
                },
                step_id=sid,
                parent="cap_sweep",
            )
        st.outputs = "accepted" if summ["accepted"] else "rejected: " + "; ".join(summ["why_not"])
        st.summary = f"{cap} W: {st.outputs}"
        st.add("energy per request change", e["mean"], "%")
        st.add("energy change, 95% interval ±", e["ci95_half"], "%")
        st.add("p99 change", p["mean"], "%")
        st.add("answers pass at cap", q["cap_pct"], "%")
        st.add("answers pass at stock", q["stock_pct"], "%")
        st.add("mean power at cap", summ["mean_w"]["cap"], "W")
        st.add("mean power at stock", summ["mean_w"]["stock"], "W")
        st.add("hottest GPU reading", summ["max_temp_c"], "°C")
        st.extra = {"accepted": summ["accepted"], "cap_w": cap}
    print("STEP_JSON " + json.dumps({k: v for k, v in summ.items() if k != "blocks"}), flush=True)
    return summ


def cmd_apply(rig: Rig, s: Stream, cap: int, parent: str = "keep_rollback") -> dict:
    """Set the chosen cap and leave it set (the caller keeps the GPU's limit saved)."""
    from wattnot.probe import CLOCK_SM

    nv, h = rig.nvml, rig.handle
    with s.step(
        "keep_rollback.apply",
        "apply",
        "cap",
        f"Apply the {cap} W cap and read it back",
        parent=parent,
        inputs=f"power cap {cap} W on GPU 0",
    ) as st:
        got = rig.set_cap(cap)
        time.sleep(rig.settle_s)
        r = {
            "cap_w": got,
            "readback_w": int(nv.power_limit_mw(h)) // 1000,
            "power_w": nv.power_usage_mw(h) / 1000.0,
            "temp_c": int(nv.temperature_c(h)),
            "sm_mhz": int(nv.clock_mhz(h, CLOCK_SM)),
        }
        s.emit(
            "action.applied",
            {"change": {"power_cap_w": got}, "kept": True, "label": s.label},
            step_id="keep_rollback.apply",
            parent=parent,
        )
        st.outputs = f"driver read back {r['readback_w']} W"
        st.numbers.append(num("cap read back", r["readback_w"], "W", s.label))
    print("APPLY_JSON " + json.dumps(r), flush=True)
    return r
