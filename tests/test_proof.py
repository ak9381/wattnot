"""The proof run's logic, on a fake GPU: pairing, the gate decision, the planner and the control."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from wattnot.proof.client import Reply
from wattnot.proof.planner import BisectPlanner, MusePlanner, allowed_caps
from wattnot.proof.rig import Rig
from wattnot.proof.run import best_of, run_session, write_report
from wattnot.proof.session import run_control, run_step
from wattnot.proof.stats import ci95, pct_change
from wattnot.runner.workload import load_workload

ROOT = Path(__file__).resolve().parents[1]
WL = load_workload(ROOT / "workloads" / "proof-v1.jsonl", kind="proof")
GOLD = {r["id"]: r["output"] for r in map(json.loads, (ROOT / "workloads" / "proof-v1.gold.jsonl")
                                           .read_text().splitlines())}
BY_PROMPT = {i.prompt: i.id for i in WL}


class FakeNvml:
    def __init__(self):
        self.limit_mw, self.energy_mj, self.lock = 700_000, 0, threading.Lock()

    def power_limit_constraints_mw(self, h): return 200_000, 700_000
    def power_default_limit_mw(self, h): return 700_000
    def power_limit_mw(self, h): return self.limit_mw
    def set_power_limit_mw(self, h, mw): self.limit_mw = mw
    def total_energy_mj(self, h): return self.energy_mj
    def power_usage_mw(self, h): return 400_000
    def temperature_c(self, h): return 55
    def clock_mhz(self, h, kind): return 1500


class FakeClient:
    """Energy per request falls with the cap; p99 rises below `knee_w`; optional wrong answers."""

    def __init__(self, nvml, *, knee_w=350, break_quality_below=None):
        self.nvml, self.knee, self.break_q = nvml, knee_w, break_quality_below
        self.n = 0

    def ask(self, prompt, max_tokens):
        cap = self.nvml.limit_mw // 1000
        rid = BY_PROMPT[prompt]
        with self.nvml.lock:
            self.n += 1
            self.nvml.energy_mj += int((1000 - (700 - cap) * 0.6 + (self.n % 7)) * 1000 * 0.05)
        lat = 1.0 + (0.5 if cap < self.knee else 0.03 * (700 - cap) / 100) + (self.n % 5) * 0.01
        text = GOLD[rid]
        finish = "stop"
        if max_tokens < 0.5 * next(i.max_tokens for i in WL if i.id == rid):
            text, finish = text[: len(text) // 3], "length"
        if self.break_q is not None and cap < self.break_q and self.n % 3 == 0:
            text = "I don't know"
        return Reply(lat, 100, text, finish)


def make_rig(**kw):
    nvml = FakeNvml()
    rig = Rig(nvml, object(), FakeClient(nvml, **kw), WL, concurrency=8, repeat=1, settle_s=0,
              warmup=2, sleep=lambda s: None)
    return rig


def test_request_tokens_floors_reasoning_models_but_not_the_bad_control():
    from wattnot.proof.rig import request_tokens
    # a proof-v1 QA item: 32-token cap. On the normal path a reasoning model is floored to 1024
    # (the gap test cut 64/64 QA answers off at 32); a non-reasoning model is left at 32.
    assert request_tokens(32, 1.0, reasoning_floor=1024) == 1024
    assert request_tokens(32, 1.0, reasoning_floor=0) == 32
    # the deliberately-short control (token_scale < 1) stays truncated even with a floor, or the
    # gate self-check could never reject a bad setting
    assert request_tokens(32, 0.1, reasoning_floor=1024) == 8   # max(8, 3), not floored
    assert request_tokens(900, 0.25, reasoning_floor=1024) == 225


class _RecordingClient:
    """Captures the max_tokens each request was sent, so a test can see the floor applied."""

    def __init__(self, nvml):
        self.nvml = nvml
        self.seen: list[int] = []

    def ask(self, prompt, max_tokens):
        self.seen.append(max_tokens)
        with self.nvml.lock:
            self.nvml.energy_mj += 1000   # advance the energy counter so the block records
        return Reply(0.01, 10, GOLD[BY_PROMPT[prompt]], "stop")


def test_rig_floors_qa_items_on_a_reasoning_model(tmp_path):
    # proof-v1's QA items carry a 32-token cap; with a reasoning floor the rig must send >= 1024
    # for them on the metered pass (token_scale 1.0), so Glimmer's answers are not cut off.
    nvml = FakeNvml()
    client = _RecordingClient(nvml)
    rig = Rig(nvml, object(), client, WL, concurrency=4, repeat=1, settle_s=0, warmup=0,
              reasoning_floor=1024, sleep=lambda s: None)
    rig.block(500)
    qa_caps = [it.max_tokens for it in WL if it.id.startswith("qa")]
    assert qa_caps and min(qa_caps) < 1024                  # the raw QA caps really are small
    assert client.seen and min(client.seen) >= 1024         # every request got the floor

    client.seen.clear()
    rig.block(500, token_scale=0.1)                         # the bad control: not floored
    assert min(client.seen) < 1024


def test_step_accepts_a_good_cap(tmp_path):
    rig = make_rig()
    s = run_step(rig, 500, 3, tmp_path, workload=WL)
    assert s["accepted"], s["why_not"]
    assert s["energy_change_pct"]["mean"] < -5
    assert s["quality"]["stock_pct"] == s["quality"]["cap_pct"] == 100.0
    assert len(s["blocks"]["cap"]) == 3
    assert rig.nvml.limit_mw == 700_000  # put back


def test_step_rejects_a_cap_that_breaks_p99(tmp_path):
    s = run_step(make_rig(), 300, 3, tmp_path, workload=WL)
    assert not s["accepted"] and any("p99" in w for w in s["why_not"])


def test_step_rejects_a_cap_that_breaks_quality(tmp_path):
    s = run_step(make_rig(break_quality_below=450), 400, 3, tmp_path, workload=WL)
    assert not s["accepted"] and any("quality" in w for w in s["why_not"])


def test_control_is_caught(tmp_path):
    r = run_control(make_rig(), tmp_path, workload=WL)
    assert r["bad_setting_rejected"] and r["stock_vs_stock_accepted"]
    assert r["truncated_pass_pct"] < r["stock_pass_pct"] - 20


def test_raw_outputs_are_hashed(tmp_path):
    s = run_step(make_rig(), 600, 2, tmp_path, workload=WL)
    b = s["blocks"]["cap"][0]
    assert (tmp_path / b["raw"]).exists() and len(b["raw_sha256"]) == 64


def test_allowed_caps_and_bisect():
    assert allowed_caps(300, 700, [])[0] == 300 and 700 not in allowed_caps(300, 700, [])
    p = BisectPlanner()
    first = p.propose([])
    assert first["kind"] == "try" and first["cap_w"] == 500
    hist = [{"cap_w": 500, "accepted": True}]
    assert p.propose(hist)["cap_w"] < 500
    hist = [{"cap_w": 500, "accepted": True}, {"cap_w": 300, "accepted": False}]
    assert 300 < p.propose(hist)["cap_w"] < 500


class FakeMeta:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def __call__(self, body, purpose=""):
        self.calls += 1
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return {"choices": [{"message": {"content": json.dumps(a)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def test_muse_planner_validates_and_falls_back():
    m = MusePlanner(FakeMeta([{"kind": "try", "cap_w": 425, "reason": "x"},
                              {"kind": "try", "cap_w": 421, "reason": "off list"},
                              RuntimeError("down"),
                              {"kind": "stop", "cap_w": None, "reason": "done"}]))
    assert m.propose([])["cap_w"] == 425
    bad = m.propose([])
    assert bad["by"] == "bisect-fallback" and "off the allow-list" in bad["muse_error"]
    assert m.propose([])["by"] == "bisect-fallback"
    assert m.propose([])["kind"] == "stop"


def test_session_runs_planner_then_grid_and_reports(tmp_path):
    rig = make_rig()
    log_lines = []
    planner = MusePlanner(FakeMeta([{"kind": "try", "cap_w": 450, "reason": "a"},
                                    {"kind": "try", "cap_w": 400, "reason": "b"},
                                    {"kind": "stop", "cap_w": None, "reason": "c"}]))
    steps = run_session(lambda c: run_step(rig, c, 2, tmp_path, workload=WL), planner,
                        (600, 400, 300), lambda k, d: log_lines.append((k, d)), max_proposals=4)
    caps = [s["cap_w"] for s in steps]
    assert caps == [450, 400, 600, 300]  # 400 was already measured by the planner
    assert steps[0]["chosen_by"] == "muse-spark" and steps[2]["chosen_by"] == "grid"
    best = best_of(steps)
    assert best and best["accepted"]
    res = write_report(tmp_path, "m", None, steps, {})
    assert res["best_cap_w"] == best["cap_w"]
    assert (tmp_path / "MANIFEST.sha256").read_text().count("\n") > 10
    assert "| 450 |" in (tmp_path / "results.md").read_text()


def test_stats():
    assert round(pct_change(80, 100), 6) == -20
    m, h = ci95([-20.0, -21.0, -19.0])
    assert m == -20.0 and 2 < h < 3
