"""A fake GPU and a fake model server, for the unit tests and for the rehearsal.

Nothing here is a measurement. Every event a rehearsal emits through these is labelled MOCK.
Energy per request falls with the cap, p99 rises below `knee_w`, and `break_quality_below` makes
some answers wrong, so the gate has something to catch.
"""

from __future__ import annotations

import json
import threading
import time

from wattnot.proof.client import Reply
from wattnot.runner.workload import load_workload, workloads_dir


def proof_workload():
    return load_workload(workloads_dir() / "proof-v1.jsonl", kind="proof")


def gold_answers() -> dict[str, str]:
    p = workloads_dir() / "proof-v1.gold.jsonl"
    return {r["id"]: r["output"] for r in map(json.loads, p.read_text().splitlines())}


class FakeNvml:
    def __init__(self) -> None:
        self.limit_mw, self.energy_mj, self.lock = 700_000, 0, threading.Lock()
        self.busy_until = 0.0

    def device_handle(self, i): return object()
    def device_name(self, h): return "NVIDIA H100 80GB HBM3 (FAKE)"
    def power_limit_constraints_mw(self, h): return 200_000, 700_000
    def power_default_limit_mw(self, h): return 700_000
    def power_limit_mw(self, h): return self.limit_mw
    def set_power_limit_mw(self, h, mw): self.limit_mw = mw
    def total_energy_mj(self, h): return self.energy_mj
    def clock_mhz(self, h, kind): return 1500 if self.limit_mw >= 500_000 else 1100

    def _busy(self) -> bool:
        return time.time() < self.busy_until

    def power_usage_mw(self, h):
        cap = self.limit_mw // 1000
        return int((min(cap, 520) if self._busy() else 90) * 1000 * 0.97)

    def temperature_c(self, h):
        return 44 + int(0.045 * self.power_usage_mw(h) / 1000) if self._busy() else 36


class FakeClient:
    def __init__(self, nvml: FakeNvml, *, knee_w: int = 350, break_quality_below=None,
                 delay_s: float = 0.0) -> None:
        self.nvml, self.knee, self.break_q, self.delay = nvml, knee_w, break_quality_below, delay_s
        self.n = 0
        self.wl = proof_workload()
        self.gold = gold_answers()
        self.by_prompt = {i.prompt: i for i in self.wl}

    def ask(self, prompt: str, max_tokens: int) -> Reply:
        cap = self.nvml.limit_mw // 1000
        it = self.by_prompt[prompt]
        with self.nvml.lock:
            self.n += 1
            self.nvml.energy_mj += int((1000 - (700 - cap) * 0.6 + (self.n % 7)) * 1000 * 0.05)
            self.nvml.busy_until = time.time() + 1.5
        if self.delay:
            time.sleep(self.delay)
        lat = 1.0 + (0.5 if cap < self.knee else 0.03 * (700 - cap) / 100) + (self.n % 5) * 0.01
        text, finish = self.gold[it.id], "stop"
        if max_tokens < 0.5 * it.max_tokens:
            text, finish = text[: len(text) // 3], "length"
        if self.break_q is not None and cap < self.break_q and self.n % 3 == 0:
            text = "I don't know"
        return Reply(lat, 100, text, finish)


class FakeMeta:
    """Stands in for the Meta client in a rehearsal and in tests: returns scripted JSON answers.
    No network, no spend."""

    def __init__(self, answers) -> None:
        self.answers, self.calls = list(answers), 0

    def __call__(self, body, purpose=""):
        self.calls += 1
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return {"model": "fake-muse", "choices": [{"message": {"content": json.dumps(a)}}],
                "usage": {"prompt_tokens": 410, "completion_tokens": 38}}


def rehearsal_planner_answers() -> list[dict]:
    return [{"kind": "try", "cap_w": 500, "reason": "p99 has headroom at 700 W; start in the middle"},
            {"kind": "try", "cap_w": 400, "reason": "500 W passed with energy down; push lower"},
            {"kind": "try", "cap_w": 300, "reason": "400 W passed; probe the floor"},
            {"kind": "stop", "cap_w": None, "reason": "300 W broke the p99 limit; 400 W is the edge"}]


def make_rig_parts(*, delay_s: float = 0.0, **kw):
    nvml = FakeNvml()
    return nvml, FakeClient(nvml, delay_s=delay_s, **kw)


