import json

import pytest

from wattnot.meter import arm_energy as ae


class FakeGpu:
    def __init__(self, fail_set=False, no_counter=False):
        self.lim, self.e, self.sets = 700, 0.0, []
        self.fail_set, self.no_counter = fail_set, no_counter

    def limit_w(self):
        return self.lim

    def set_limit_w(self, w):
        if self.fail_set and w != 700:
            raise RuntimeError("no perms")
        self.sets.append(w)
        self.lim = w

    def energy_j(self):
        return None if self.no_counter else self.e

    def power_w(self):
        return float(self.lim)


def _runner(gpu, calls):
    def once(budget):
        calls.append((gpu.lim, budget))
        gpu.e += gpu.lim * 0.1   # joules scale with the limit
        return ("res", gpu.lim), 10, True
    return once


def test_two_arms_measures_both_and_restores(monkeypatch):
    g = FakeGpu()
    monkeypatch.setattr(ae, "Gpu", lambda: g)
    monkeypatch.setattr(ae, "best_cap_w", lambda: 350)
    monkeypatch.setattr(ae.time, "sleep", lambda s: None)
    calls = []
    res, doc = ae.two_arms(_runner(g, calls), items=10, unit="image", budget_s=90, live=True)
    assert res == ("res", 700)               # the block reports the first (gated) run
    assert [c[0] for c in calls] == [700, 350]
    assert g.lim == 700                       # restored
    arms = doc["energy_arms"]
    assert arms["without_wattnot"]["j_per_image"] == 7.0
    assert arms["with_wattnot"]["j_per_image"] == 3.5
    assert doc["savings_pct_image"] == 50.0
    json.dumps(doc)


def test_cap_set_failure_never_fails_block(monkeypatch):
    g = FakeGpu(fail_set=True)
    monkeypatch.setattr(ae, "Gpu", lambda: g)
    monkeypatch.setattr(ae, "best_cap_w", lambda: 350)
    res, doc = ae.two_arms(_runner(g, []), items=10, unit="clip", budget_s=90, live=True)
    assert res == ("res", 700) and g.lim == 700
    assert "energy_error" in doc or "with_wattnot" not in doc.get("energy_arms", {})


def test_no_nvml_runs_once(monkeypatch):
    def boom():
        raise ImportError("pynvml")
    monkeypatch.setattr(ae, "Gpu", boom)
    calls = []
    res, doc = ae.two_arms(lambda b: (calls.append(b) or "r", 1, True), items=1, unit="image",
                           budget_s=90, live=True)
    assert res == "r" and doc is None and calls == [90]


def test_fake_mode_skips_energy():
    calls = []
    res, doc = ae.two_arms(lambda b: (calls.append(b) or "r", 1, True), items=1, unit="image",
                           budget_s=90, live=False)
    assert doc is None and len(calls) == 1


def test_no_best_cap_single_arm(monkeypatch):
    g = FakeGpu()
    monkeypatch.setattr(ae, "Gpu", lambda: g)
    monkeypatch.setattr(ae, "best_cap_w", lambda: None)
    calls = []
    _res, doc = ae.two_arms(_runner(g, calls), items=10, unit="image", budget_s=90, live=True)
    assert len(calls) == 1 and "with_wattnot" not in doc["energy_arms"]


def test_best_cap_file(tmp_path, monkeypatch):
    f = tmp_path / "cap"
    f.write_text("350\n")
    monkeypatch.setattr(ae, "BEST_CAP_FILE", f)
    monkeypatch.delenv("WATTNOT_BEST_CAP_W", raising=False)
    assert ae.best_cap_w() == 350


def test_phase_metrics_base_strips_v1():
    from wattnot.control.phase_clock import metrics_base
    assert metrics_base("http://127.0.0.1:8000/v1") == "http://127.0.0.1:8000"
    assert metrics_base("http://127.0.0.1:8000/v1/") == "http://127.0.0.1:8000"
    assert metrics_base("http://127.0.0.1:8000") == "http://127.0.0.1:8000"


def _live(monkeypatch, **kw):
    g = FakeGpu()
    monkeypatch.setattr(ae, "Gpu", lambda: g)
    monkeypatch.setattr(ae, "best_cap_w", lambda: 350)   # stale file must be ignored
    monkeypatch.setattr(ae.time, "sleep", lambda s: None)
    calls = []
    _res, doc = ae.two_arms(_runner(g, calls), items=10, unit="image", budget_s=90,
                            live=True, **kw)
    return doc, calls


def test_uses_pass_sweep_winner_and_records_source(monkeypatch):
    doc, calls = _live(monkeypatch, best_cap="375", cap_source="sweep:p2-sweep")
    assert [c[0] for c in calls] == [700, 375]
    assert doc["energy_arms"]["with_wattnot"]["limit_w"] == 375
    assert doc["cap_w"] == 375 and doc["cap_source"] == "sweep:p2-sweep"


def test_no_sweep_winner_falls_back_to_350(monkeypatch):
    doc, calls = _live(monkeypatch, best_cap="NONE", cap_source="sweep:p2-sweep")
    assert [c[0] for c in calls] == [700, 350]
    assert doc["cap_w"] == 350 and doc["cap_source"] == "fallback_350w_no_sweep_winner"


def test_resolve_cap_legacy_file(monkeypatch):
    monkeypatch.delenv("WATTNOT_BEST_CAP_W", raising=False)
    monkeypatch.setattr(ae, "best_cap_w", lambda: 325)
    cap, src = ae.resolve_cap()
    assert cap == 325 and src.startswith("file:")


def test_driver_passes_own_sweep_cap_to_eye_and_spoken(tmp_path):
    from wattnot.longrun import driver as d
    for pas, tag in (("p2", "p2-sweep"), ("p1r", "p1r-sweep")):
        if pas not in d.OWN_CAP_PASSES:
            continue
        bl = {b.id: b for b in d.pass_blocks(tmp_path, pas, tag, "325:450:25", "gsm8k",
                                               optional=True, sweep_dry=["true"])}
        for k in ("eye", "spoken"):
            b = bl[f"{pas}-{k}"]
            assert "{BEST_CAP}" in b.live and f"sweep:{tag}" in b.live
            assert b.sweep_tag == tag
