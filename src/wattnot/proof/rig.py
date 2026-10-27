"""Run one metered block of requests at one power cap and grade it.

Energy comes from the GPU's own cumulative counter (NVML total energy, millijoules), read
before and after the block: MEASURED. A 2 Hz sampler runs alongside only to log power,
temperature and SM clock as a sanity check on the counter.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from wattnot.gates.fixed import pass_rate
from wattnot.probe import CLOCK_SM
from wattnot.proof.client import ChatClient, Reply
from wattnot.proof.stats import percentile
from wattnot.runner.workload import Workload, WorkloadItem


def pooled_items(workload: Workload, copies: int) -> list[WorkloadItem]:
    """The workload `copies` times with ids `<id>#<copy>`, so one gate call grades them all."""
    return [dataclasses.replace(it, id=f"{it.id}#{c}") for c in range(copies) for it in workload]


class Rig:
    def __init__(self, nvml, handle, client: ChatClient, workload: Workload, *,
                 concurrency: int = 32, repeat: int = 2, settle_s: float = 4.0, warmup: int = 16,
                 sleep: Callable[[float], None] = time.sleep,
                 on_phase: Callable[..., None] | None = None,
                 on_sample: Callable[[dict], None] | None = None) -> None:
        self.nvml, self.handle, self.client, self.workload = nvml, handle, client, workload
        self.concurrency, self.repeat, self.settle_s, self.warmup = (
            concurrency, repeat, settle_s, warmup)
        self._sleep = sleep
        self.on_phase, self.on_sample = on_phase, on_sample
        lo, hi = nvml.power_limit_constraints_mw(handle)
        self.min_w, self.max_w = lo // 1000, hi // 1000
        self.default_w = int(nvml.power_default_limit_mw(handle)) // 1000

    def set_cap(self, watts: int) -> int:
        w = max(self.min_w, min(self.max_w, int(watts)))
        self.nvml.set_power_limit_mw(self.handle, w * 1000)
        got = int(self.nvml.power_limit_mw(self.handle)) // 1000
        if got != w:
            raise RuntimeError(f"power cap readback {got} W, asked {w} W")
        return w

    def _ph(self, name: str, state: str, **info) -> None:
        if self.on_phase:
            self.on_phase(name, state, info)

    def block(self, cap_w: int, *, token_scale: float = 1.0) -> dict:
        """Set the cap, settle, warm up, then one metered pass. `token_scale` < 1 is the
        deliberately bad control (generation cut short)."""
        self._ph("settle", "start", asked_w=cap_w)
        cap = self.set_cap(cap_w)
        self._sleep(self.settle_s)
        self._ph("settle", "end", cap_w=cap,
                 readback_w=int(self.nvml.power_limit_mw(self.handle)) // 1000,
                 settle_s=self.settle_s)
        items = list(self.workload)
        self._ph("warmup", "start", n=self.warmup)
        for k in range(self.warmup):
            it = items[k % len(items)]
            self.client.ask(it.prompt, max(8, int(it.max_tokens * token_scale)))
        self._ph("warmup", "end", n=self.warmup)
        reqs = [(c, it) for c in range(self.repeat) for it in items]
        samples: list[tuple[float, float, int, int]] = []
        stop = threading.Event()

        def sampler() -> None:
            while not stop.is_set():
                try:
                    samples.append((time.time(), self.nvml.power_usage_mw(self.handle) / 1000.0,
                                    int(self.nvml.temperature_c(self.handle)),
                                    int(self.nvml.clock_mhz(self.handle, CLOCK_SM))))
                    if self.on_sample:
                        _, w, tc, mhz = samples[-1]
                        self.on_sample({"power_w": w, "temp_c": tc, "sm_mhz": mhz, "cap_w": cap})
                except Exception:  # noqa: BLE001 - sanity log only
                    pass
                stop.wait(0.5)

        th = threading.Thread(target=sampler, daemon=True)
        th.start()
        self._ph("metered", "start", requests=len(reqs), concurrency=self.concurrency)
        e0, t0 = int(self.nvml.total_energy_mj(self.handle)), time.perf_counter()
        try:
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                replies: list[Reply] = list(pool.map(
                    lambda ci: self.client.ask(ci[1].prompt,
                                               max(8, int(ci[1].max_tokens * token_scale))), reqs))
        finally:
            e1, t1 = int(self.nvml.total_energy_mj(self.handle)), time.perf_counter()
            stop.set()
            th.join(timeout=2)
        joules, wall = (e1 - e0) / 1000.0, t1 - t0
        self._ph("metered", "end", requests=len(replies), joules=joules, wall_s=wall,
                 p50_s=percentile([r.latency_s for r in replies], 50),
                 p99_s=percentile([r.latency_s for r in replies], 99),
                 tokens=sum(r.tokens for r in replies),
                 errors=sum(r.finish == "error" for r in replies),
                 mean_w=joules / wall if wall else None)
        self._ph("grade", "start", prompts=len(self.workload))
        outputs = {f"{it.id}#{c}": r.text for (c, it), r in zip(reqs, replies, strict=True)}
        cut = {f"{it.id}#{c}": r.finish != "stop" for (c, it), r in zip(reqs, replies, strict=True)}
        rate, n_checked = pass_rate(outputs, pooled_items(self.workload, self.repeat),
                                    truncated=cut)
        lats = [r.latency_s for r in replies]
        n = len(replies)
        self._ph("grade", "end", pass_pct=rate, checked=n_checked, truncated=sum(cut.values()))
        return {
            "cap_w": cap, "token_scale": token_scale, "label": "MEASURED",
            "requests": n, "errors": sum(r.finish == "error" for r in replies),
            "truncated": sum(cut.values()), "tokens": sum(r.tokens for r in replies),
            "joules": joules, "wall_s": wall, "j_per_req": joules / n,
            "j_per_token": joules / max(1, sum(r.tokens for r in replies)),
            "p50_s": percentile(lats, 50), "p99_s": percentile(lats, 99),
            "pass_pct": rate, "checked": n_checked,
            "sampled_mean_w": (sum(s[1] for s in samples) / len(samples)) if samples else None,
            "counter_mean_w": joules / wall if wall else None,
            "max_temp_c": max((s[2] for s in samples), default=None),
            "mean_sm_mhz": (sum(s[3] for s in samples) / len(samples)) if samples else None,
            "outputs": outputs, "cut": cut,
        }
