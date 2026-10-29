"""Focused tests for the proof modules' pure helpers.

No network, no GPU: stats math, workload pooling, cap clamping, report
helpers, raw-output saving and the gate summary on hand-built blocks.
"""

from __future__ import annotations

import hashlib
import json
import math

from wattnot.proof import run as prun
from wattnot.proof import session as psess
from wattnot.proof import stats as st
from wattnot.proof.client import ChatClient
from wattnot.proof.rig import Rig, pooled_items
from wattnot.runner.workload import Workload, WorkloadItem


def _item(i="q"):
    return WorkloadItem(i, "Capital of France?", 16,
                        {"checks": [{"type": "contains", "value": "Paris"}]})


def _workload():
    return Workload("t", "proof", (_item(),), "v1")


def _block(j_per_req, p99_s, text, *, tokens=100):
    return {"j_per_req": j_per_req, "p99_s": p99_s, "p50_s": p99_s / 2,
            "j_per_token": j_per_req / tokens, "counter_mean_w": 400.0,
            "max_temp_c": 55, "errors": 0, "tokens": tokens,
            "outputs": {"q#0": text}, "cut": {"q#0": False}}


# --- stats -------------------------------------------------------------------


def test_t95_known_values_and_fallback():
    assert st.t95(1) == 12.706
    assert st.t95(3) == 3.182
    assert st.t95(10) == 2.228
    assert st.t95(11) == 2.0
    assert st.t95(1000) == 2.0
    assert math.isnan(st.t95(0))


def test_mean_sd_empty_single_and_many():
    m, sd = st.mean_sd([])
    assert math.isnan(m) and math.isnan(sd)
    assert st.mean_sd([4.0]) == (4.0, 0.0)
    m, sd = st.mean_sd([1.0, 2.0, 3.0])
    assert m == 2.0 and sd == 1.0


def test_ci95_needs_two_points():
    m, half = st.ci95([5.0])
    assert m == 5.0 and math.isnan(half)
    m, half = st.ci95([-20.0, -21.0, -19.0])
    assert m == -20.0 and 0 < half < 5


def test_pct_change_zero_old_is_nan():
    assert st.pct_change(80.0, 100.0) == -20.0
    assert math.isnan(st.pct_change(5.0, 0.0))


def test_percentile_endpoints_and_interpolation():
    assert math.isnan(st.percentile([], 50))
    assert st.percentile([3.0], 95) == 3.0
    assert st.percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert st.percentile([1.0, 2.0, 3.0, 4.0], 0) == 1.0
    assert st.percentile([1.0, 2.0, 3.0, 4.0], 100) == 4.0


# --- rig ---------------------------------------------------------------------


def test_pooled_items_repeat_ids_without_touching_the_originals():
    wl = Workload("t", "proof", (_item("a"), _item("b")), "v1")
    got = pooled_items(wl, 2)
    assert [i.id for i in got] == ["a#0", "b#0", "a#1", "b#1"]
    assert [i.id for i in wl] == ["a", "b"]


class _Nvml:
    def __init__(self, readback=None):
        self.set_to = None
        self.readback = readback

    def power_limit_constraints_mw(self, h):
        return 200_000, 700_000

    def power_default_limit_mw(self, h):
        return 700_000

    def power_limit_mw(self, h):
        return self.readback if self.readback is not None else self.set_to

    def set_power_limit_mw(self, h, mw):
        self.set_to = mw


def _rig(nvml):
    wl = _workload()
    return Rig(nvml, object(), None, wl, sleep=lambda s: None)


def test_set_cap_clamps_to_the_gpu_range():
    rig = _rig(_Nvml())
    assert rig.min_w == 200 and rig.max_w == 700 and rig.default_w == 700
    assert rig.set_cap(500) == 500
    assert rig.set_cap(50) == 200
    assert rig.set_cap(9000) == 700


def test_set_cap_rejects_a_bad_readback():
    rig = _rig(_Nvml(readback=123_000))
    try:
        rig.set_cap(500)
    except RuntimeError as e:
        assert "readback" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


# --- run ---------------------------------------------------------------------


def _step(cap_w=450, accepted=True, energy=-12.0, p99=3.0):
    return {"cap_w": cap_w, "accepted": accepted, "why_not": [],
            "energy_change_pct": {"mean": energy, "sd": 1.0},
            "p99_change_pct": {"mean": p99, "sd": 0.5},
            "quality": {"cap_pct": 100.0, "stock_pct": 100.0}}


def test_history_row_rounds_for_the_planner():
    row = prun.history_row(_step(450, energy=-12.345, p99=3.333))
    assert row == {"cap_w": 450, "accepted": True, "why_not": [],
                   "energy_change_pct": -12.35, "p99_change_pct": 3.33,
                   "quality_pass_pct": 100.0, "stock_pass_pct": 100.0,
                   "clock_profile": "base"}


def test_best_of_picks_the_biggest_saving():
    assert prun.best_of([]) is None
    assert prun.best_of([_step(450, accepted=False), _step(400, accepted=False)]) is None
    steps = [_step(500, energy=-5.0), _step(450, energy=-12.0), _step(400, accepted=False)]
    assert prun.best_of(steps)["cap_w"] == 450


def test_write_manifest_hashes_everything_but_itself(tmp_path):
    (tmp_path / "a.json").write_text('{"x": 1}')
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.json").write_text('{"y": 2}')
    prun.write_manifest(tmp_path)
    lines = (tmp_path / "MANIFEST.sha256").read_text().splitlines()
    assert len(lines) == 2
    assert lines == sorted(lines)
    assert all("MANIFEST" not in line for line in lines)
    digest = hashlib.sha256((tmp_path / "a.json").read_bytes()).hexdigest()
    assert f"{digest}  a.json" in lines


def test_make_log_appends_jsonl_and_prints(tmp_path, capsys):
    log = prun.make_log(tmp_path)
    log("decision", {"cap_w": 450})
    line = (tmp_path / "log.jsonl").read_text().strip()
    doc = json.loads(line)
    assert doc["kind"] == "decision" and doc["cap_w"] == 450 and "t" in doc
    assert "decision" in capsys.readouterr().out


# --- session -----------------------------------------------------------------


def test_pool_renumbers_copies_across_repeats():
    blocks = [{"outputs": {"q#0": "a", "q#1": "b"}, "cut": {"q#0": False, "q#1": True}},
              {"outputs": {"q#0": "c", "q#1": "d"}, "cut": {"q#0": False, "q#1": False}}]
    outs, cut = psess._pool(blocks, 2)
    assert outs == {"q#0": "a", "q#1": "b", "q#2": "c", "q#3": "d"}
    assert cut == {"q#0": False, "q#1": True, "q#2": False, "q#3": False}


def test_save_raw_writes_jsonl_and_returns_a_slim_block(tmp_path):
    block = _block(8.0, 1.0, "Paris")
    slim = psess.save_raw(tmp_path, "cap450-stock-r0", block)
    raw = tmp_path / "raw" / "cap450-stock-r0.jsonl"
    assert raw.exists()
    doc = json.loads(raw.read_text().strip())
    assert doc == {"id": "q#0", "finish_cut": False, "text": "Paris"}
    assert "outputs" not in slim and "cut" not in slim
    assert slim["raw"] == "raw/cap450-stock-r0.jsonl"
    assert slim["raw_sha256"] == hashlib.sha256(raw.read_bytes()).hexdigest()


def test_summarize_accepts_a_clear_win():
    wl = _workload()
    stock = [_block(10.0, 1.0, "It is Paris."), _block(10.0, 1.0, "Paris!")]
    capped = [_block(8.0, 1.0, "Paris."), _block(8.0, 1.0, "It is Paris.")]
    s = psess.summarize(450, stock, capped, wl, 1)
    assert s["accepted"] and s["why_not"] == []
    assert s["energy_change_pct"]["mean"] == -20.0
    assert s["quality"]["cap_pct"] == s["quality"]["stock_pct"] == 100.0
    assert s["label"] == "MEASURED"


def test_summarize_rejects_a_p99_regression():
    wl = _workload()
    stock = [_block(10.0, 1.0, "Paris."), _block(10.0, 1.0, "Paris.")]
    capped = [_block(8.0, 1.3, "Paris."), _block(8.0, 1.3, "Paris.")]
    s = psess.summarize(450, stock, capped, wl, 1)
    assert not s["accepted"]
    assert any("p99" in w for w in s["why_not"])


def test_summarize_rejects_higher_energy():
    wl = _workload()
    stock = [_block(10.0, 1.0, "Paris."), _block(10.0, 1.0, "Paris.")]
    capped = [_block(11.0, 1.0, "Paris."), _block(11.0, 1.0, "Paris.")]
    s = psess.summarize(450, stock, capped, wl, 1)
    assert not s["accepted"]
    assert "energy change not clearly below zero" in s["why_not"]


def test_summarize_rejects_a_quality_drop():
    wl = _workload()
    stock = [_block(10.0, 1.0, "Paris."), _block(10.0, 1.0, "Paris.")]
    capped = [_block(8.0, 1.0, "I don't know."), _block(8.0, 1.0, "No idea.")]
    s = psess.summarize(450, stock, capped, wl, 1)
    assert not s["accepted"]
    assert any("quality" in w for w in s["why_not"])


# --- client ------------------------------------------------------------------


def test_chat_client_url_normalization():
    assert ChatClient("http://h:8000/v1/", "m").url == "http://h:8000/v1/chat/completions"
    assert ChatClient("http://h:8000/v1", "m").url == "http://h:8000/v1/chat/completions"


def test_ask_records_a_refused_connection_as_an_error():
    client = ChatClient("http://127.0.0.1:0/v1", "m", timeout_s=5.0)
    reply = client.ask("Capital of France?", 8)
    assert reply.finish == "error" and reply.tokens == 0
    assert reply.text.startswith("[request failed: ")
