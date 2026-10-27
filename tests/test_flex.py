"""Flex mode: power model, controller, receipt, CLI, and the GPU shim on a fake NVML."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from tests.fixtures.nvml_fake import FakeNVML

from wattnot.cli import main
from wattnot.flex import signal as sig
from wattnot.flex.controller import FlexConfig
from wattnot.flex.hw import ProgressFile, run_on_gpu
from wattnot.flex.power_model import PowerModel
from wattnot.flex.receipt import build_flex_receipt, check_trace, write_flex_receipt
from wattnot.flex.run import SimBackend, run_flex
from wattnot.receipt.joules import check_joules
from wattnot.verify import load_trusted, verify_receipt

ROOT = Path(__file__).resolve().parents[1]
KEY = ROOT / "verifier" / "samples" / "test-key.pem"
SIGNAL = sig.load()


def run(i=0, **kw):
    return run_flex(SimBackend(**kw), SIGNAL.events[i])


# ---- power model -----------------------------------------------------------------


def test_cap_above_need_changes_nothing():
    m = PowerModel()
    assert m.observe(700, 10).power_w == m.observe(400, 10).power_w
    assert m.observe(700, 10).clock == 1.0


def test_lower_cap_lowers_power_and_slows_answers():
    m = PowerModel()
    caps = [270, 240, 220, 200]
    obs = [m.observe(c, 10) for c in caps]
    assert [o.power_w for o in obs] == sorted(o.power_w for o in obs)[::-1]
    assert all(obs[i].p99_ms < obs[i + 1].p99_ms for i in range(len(obs) - 1))
    assert all(o.power_w <= c + 1e-6 for o, c in zip(obs, caps, strict=True))


def test_clock_never_below_floor_and_power_may_exceed_a_tiny_cap():
    m = PowerModel()
    o = m.observe(80, 10)
    assert o.clock == m.clock_min
    assert o.power_w > 80  # the cap cannot push the card below its lowest clock


def test_saturation_is_flagged():
    o = PowerModel().observe(200, 19.5)
    assert o.saturated or o.busy > 0.95


def test_model_is_labelled_simulated():
    assert PowerModel().to_dict()["label"] == "SIMULATED"


# ---- signal ----------------------------------------------------------------------


def test_demo_signal_loads_and_is_simulated():
    s = sig.load()
    assert s.label == "SIMULATED" and len(s.events) == 2
    assert [e.target_pct for e in s.events] == [25, 35]


@pytest.mark.parametrize("doc, msg", [
    ({"events": []}, "non-empty"),
    ({"events": [{"target_pct": 0, "duration_s": 60}]}, "between 0 and 100"),
    ({"events": [{"target_pct": 20, "duration_s": 3}]}, "at least 10"),
    ({"events": [{"target_pct": "x", "duration_s": 60}]}, "required numbers"),
    ({"events": [{"kind": "boost", "target_pct": 10, "duration_s": 60}]}, "kind"),
    ({"label": "REAL", "events": [{"target_pct": 10, "duration_s": 60}]}, "label"),
])
def test_bad_signals_are_refused(tmp_path, doc, msg):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(sig.SignalError, match=msg):
        sig.load(p)


def test_missing_signal_file(tmp_path):
    with pytest.raises(sig.SignalError, match="cannot read"):
        sig.load(tmp_path / "nope.json")


# ---- controller ------------------------------------------------------------------


def test_first_event_is_met_in_seconds_and_stays_in_budget():
    r = run(0).result
    assert r["status"] == "full" and r["limit"] is None
    assert r["delivered_pct"] >= 25 * 0.98
    assert r["seconds_to_target"] <= 30
    assert r["p99_ms"]["event_mean"] <= r["p99_ms"]["sla"]
    assert r["pass_rate"]["event"] == r["pass_rate"]["before"]
    assert r["ramp_back_s"] is not None and r["ramp_back_s"] <= 15


def test_second_event_stops_short_and_says_why():
    r = run(1).result
    assert r["status"] == "partial" and r["limit"] == "cap_floor"
    assert 15 < r["delivered_pct"] < 35
    assert r["settled_cap_w"] == 200  # the card's lowest allowed limit


def test_cap_never_leaves_the_card_range():
    ru = run(1)
    caps = [t.cap_w for t in ru.ticks]
    assert min(caps) >= 200 and max(caps) <= 700


def test_cap_is_restored_after_the_event():
    ru = run(0)
    assert ru.ticks[-1].cap_w == 700


def test_tight_budget_stops_on_p99_and_holds_a_floor():
    ru = run_flex(SimBackend(), SIGNAL.events[0], FlexConfig(sla_slack=0.25))
    r = ru.result
    assert r["status"] == "partial" and r["limit"] == "p99"
    # after it backed off it never went below that cap again during the event
    ev = [t for t in ru.ticks if t.phase == "event"]
    settled = [t.cap_w for t in ev if t.t_s >= r["settled_from_s"]]
    assert min(settled) > 200


def test_quality_drop_makes_it_back_off():
    q = lambda cap: 0.82 if cap >= 240 else 0.70  # noqa: E731
    ru = run_flex(SimBackend(quality_at=q), SIGNAL.events[0])
    r = ru.result
    assert r["status"] == "partial" and r["limit"] == "quality"
    assert r["pass_rate"]["event"] >= 0.82 - 0.011


def test_run_is_repeatable_and_noise_changes_with_seed():
    a, b = run(0, seed=3).result, run(0, seed=3).result
    assert a == b
    assert run(0, seed=4).result["p99_ms"]["event_max"] != a["p99_ms"]["event_max"]


def test_cap_is_restored_even_if_the_run_crashes():
    class Boom(SimBackend):
        def observe(self, t):
            if t > 40:
                raise RuntimeError("meter died")
            return super().observe(t)

    b = Boom()
    with pytest.raises(RuntimeError):
        run_flex(b, SIGNAL.events[0])
    assert b.cap_w() == 700


def test_config_is_checked():
    with pytest.raises(ValueError):
        FlexConfig(ema_alpha=0).validate()
    with pytest.raises(ValueError):
        FlexConfig(headroom=0.99, danger=0.9).validate()
    with pytest.raises(ValueError):
        run_flex(SimBackend(), SIGNAL.events[0], FlexConfig(pre_s=2))


# ---- receipt ---------------------------------------------------------------------


def test_receipt_is_simulated_and_never_says_measured():
    r, _, _ = build_flex_receipt(run(0), SIGNAL, created_at="2026-10-27T10:00:00Z")
    assert r["grade"] == "SIMULATED" and r["profile"] == "flex"
    assert r["measurement"]["label"] == "SIMULATED"
    assert "MEASURED" not in json.dumps(r["flex"])
    assert r["result"]["welch"]["applicable"] is False and r["result"]["billable"] is False
    assert r["flex"]["scope"].startswith("GPU draw only")
    assert "carbon" in " ".join(r["flex"]["not_claimed"])
    assert "neutral" not in json.dumps(r).lower()
    assert check_joules(r)[0]


def test_receipt_is_repeatable_byte_for_byte():
    a = build_flex_receipt(run(0), SIGNAL, created_at="2026-10-27T10:00:00Z")
    b = build_flex_receipt(run(0), SIGNAL, created_at="2026-10-27T10:00:00Z")
    assert a[0] == b[0] and a[1] == b[1]


def test_receipt_chain_and_both_events(tmp_path):
    w1 = write_flex_receipt(run(0), SIGNAL, tmp_path, created_at="2026-10-27T10:00:00Z")
    w2 = write_flex_receipt(run(1), SIGNAL, tmp_path, created_at="2026-10-27T10:00:00Z",
                            prev_sha256=w1["receipt"]["integrity"]["sha256"])
    assert w2["receipt"]["integrity"]["prev_sha256"] == w1["receipt"]["integrity"]["sha256"]
    assert w1["receipt"]["flex"]["outcome"]["status"] == "full"
    assert w2["receipt"]["flex"]["outcome"]["status"] == "partial"


def test_signed_receipt_verifies_with_both_verifiers(tmp_path):
    import importlib.util

    w = write_flex_receipt(run(0), SIGNAL, tmp_path, created_at="2026-10-27T10:00:00Z",
                           sign_key=str(KEY))
    rep = verify_receipt(w["path"], None, trusted=load_trusted())
    assert rep.grade == "SIMULATED" and rep.joules
    spec = importlib.util.spec_from_file_location("sv", ROOT / "verifier" / "wattnot_verify.py")
    sv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sv)
    trusted = sv.load_trusted(ROOT / "verifier" / "trusted-keys.json")
    out = sv.verify_path(w["path"], trusted=trusted)
    assert out["grade"] == "SIMULATED"


def test_edited_flex_receipt_is_caught(tmp_path):
    w = write_flex_receipt(run(0), SIGNAL, tmp_path, created_at="2026-10-27T10:00:00Z",
                           sign_key=str(KEY))
    p = Path(w["path"])
    doc = json.loads(p.read_text())
    doc["flex"]["outcome"]["delivered_pct"] = 40.0
    p.write_text(json.dumps(doc))
    rep = verify_receipt(p, None, trusted=load_trusted())
    assert rep.grade == "UNVERIFIABLE"


def test_trace_check_catches_a_changed_trace(tmp_path):
    w = write_flex_receipt(run(0), SIGNAL, tmp_path, created_at="2026-10-27T10:00:00Z")
    ok, problems = check_trace(w["path"])
    assert ok and not problems
    csv_path = next(tmp_path.glob("*.trace.csv"))
    csv_path.write_text(csv_path.read_text().replace("power_w", "power_x", 1))
    ok, problems = check_trace(w["path"])
    assert not ok and any("sha256" in p for p in problems)
    csv_path.unlink()
    assert check_trace(w["path"])[0] is False


def test_trace_json_matches_csv_points(tmp_path):
    w = write_flex_receipt(run(0), SIGNAL, tmp_path, created_at="2026-10-27T10:00:00Z")
    tj = json.loads(next(tmp_path.glob("*.trace.json")).read_text())
    assert tj["label"] == "SIMULATED"
    assert len(tj["points"]) == w["receipt"]["flex"]["trace"]["points"]
    keys = {"t_s", "phase", "power_w", "cap_w", "p99_ms", "target_w", "sla_ms"}
    assert keys <= tj["points"][0].keys()
    assert {p["phase"] for p in tj["points"]} == {"before", "event", "after"}


def test_check_trace_on_non_flex_receipt():
    fx = ROOT / "tests" / "fixtures" / "receipts" / "valid.json"
    assert check_trace(fx) == (False, ["not a flex receipt (no flex block)"])


# ---- CLI -------------------------------------------------------------------------


def test_cli_run_and_show(tmp_path, capsys):
    assert main(["flex", "run", "--out", str(tmp_path), "--sign-key", str(KEY),
                 "--created-at", "2026-10-27T10:00:00Z"]) == 0
    out = capsys.readouterr().out
    assert "[SIMULATED] FULL" in out and "[SIMULATED] PARTIAL" in out and "cap_floor" in out
    receipts = sorted(p for p in tmp_path.glob("rcpt_flex_*.json")
                      if ".trace" not in p.name and ".sigstore" not in p.name)
    assert len(receipts) == 2
    assert main(["flex", "show", str(receipts[0])]) == 0
    assert "trace ok" in capsys.readouterr().out
    assert main(["flex", "show", "--json", str(receipts[0])]) == 0
    assert json.loads(capsys.readouterr().out)["trace_ok"] is True


def test_cli_json_output_and_event_filter(tmp_path, capsys):
    assert main(["flex", "run", "--out", str(tmp_path), "--event", "demo-evt-1", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [d["event"] for d in data] == ["demo-evt-1"] and data[0]["grade"] == "SIMULATED"


def test_cli_unknown_event_is_an_error(tmp_path):
    with pytest.raises(SystemExit):
        main(["flex", "run", "--out", str(tmp_path), "--event", "nope"])


def test_cli_show_refuses_non_flex(capsys):
    with pytest.raises(SystemExit):
        main(["flex", "show", str(ROOT / "tests" / "fixtures" / "receipts" / "valid.json")])


# ---- GPU shim, on a fake NVML ----------------------------------------------------


def test_progress_file_reads_last_good_line(tmp_path):
    p = tmp_path / "p.jsonl"
    pf = ProgressFile(p)
    with pytest.raises(RuntimeError):
        pf.latest()
    p.write_text('{"p99_ms": 100, "pass_rate": 0.8, "rps": 5}\n{"p99_ms": 1')
    assert pf.latest()["p99_ms"] == 100  # half-written last line is skipped


def test_gpu_run_on_fake_nvml_restores_the_limit(tmp_path):
    p = tmp_path / "p.jsonl"
    p.write_text('{"p99_ms": 1000, "pass_rate": 0.8, "rps": 8}\n')
    nv = FakeNVML(power_mw=300_000, power_limit_mw=650_000)
    cfg = FlexConfig(pre_s=6, post_s=6)
    ev = sig.ShedEvent("hw-1", 20.0, 20)
    ru = run_on_gpu(nv, ev, p, cfg=cfg, sleep=lambda s: None)
    assert nv.current_limit_mw == 650_000  # put back
    assert ru.meter["label"] == "MEASURED"
    assert ru.ticks[0].power_w == 300.0
    # the fake draw never falls, so the target is not reached: reported as it is
    assert ru.result["status"] in ("none", "partial")


def test_gpu_receipt_is_estimate_not_valid(tmp_path):
    p = tmp_path / "p.jsonl"
    p.write_text('{"p99_ms": 1000, "pass_rate": 0.8, "rps": 8}\n')
    nv = FakeNVML(power_mw=300_000, power_limit_mw=650_000)
    ru = run_on_gpu(nv, sig.ShedEvent("hw-1", 20.0, 20), p,
                    cfg=FlexConfig(pre_s=6, post_s=6), sleep=lambda s: None)
    r, _, _ = build_flex_receipt(ru, SIGNAL, created_at="2026-10-27T10:00:00Z")
    assert r["measurement"]["label"] == "MEASURED" and r["grade"] == "ESTIMATE"


def test_demo_signal_file_not_edited_by_tests(tmp_path):
    copy = tmp_path / "s.json"
    shutil.copy(sig.DEFAULT_SIGNAL, copy)
    assert sig.load(copy).events == sig.load().events
