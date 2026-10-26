from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.fixtures.nvml_fake import FakeNVML

from wattnot import recommend as R
from wattnot.cli import main
from wattnot.control import CapGuard, step_down
from wattnot.events.emitter import Emitter
from wattnot.impact import estimate
from wattnot.planner import Choice, Decision

EXAMPLE = Path(__file__).parent / "fixtures" / "runs" / "example_report.json"


def _run(**kw):
    base = dict(start_cap_w=700, final_cap_w=550, gate_passed=True, base_wh_per_req=0.048,
                final_wh_per_req=0.041, base_p99_ms=1000, final_p99_ms=1070)
    return R.for_run(**{**base, **kw})


def _by_key(rec):
    return {a.key: a for a in rec.actions}


def test_cap_headroom_and_extra_gpus():
    rec = _run()
    a = _by_key(rec)
    assert rec.verdict == "change recommended"
    assert a["power_cap_w"].value == 550 and a["power_cap_w"].label == "MEASURED"
    assert a["headroom_w_per_gpu"].value == 150
    assert a["headroom_w_per_rack"].value == 1200 and a["headroom_w_per_rack"].label == "ESTIMATE"
    # 8 x 700 = 5600 W budget: 5600 // 550 - 5600 // 700 = 10 - 8
    assert a["extra_gpus_per_rack"].value == 2 and "5,600 W" in a["extra_gpus_per_rack"].text
    assert rec.assumptions["rack_budget_note"] == "GPU power only, assumed"


def test_given_rack_budget_uses_floor_division():
    a = _by_key(_run(rack_budget_w=6000))
    assert a["extra_gpus_per_rack"].value == 6000 // 550 - 6000 // 700


def test_money_and_water_match_impact_and_are_estimates():
    a = _by_key(_run())
    i = estimate(0.048 - 0.041, 100_000, 8)
    assert a["usd_saved"].value == round(i.usd, 2) and a["usd_saved"].unit == "USD"
    assert a["water_saved_l"].label == "ESTIMATE" and a["water_saved_l"].value == round(i.litres)


def test_every_action_is_labelled():
    for rec in (_run(), _run(gate_passed=False, final_cap_w=700), _run(final_wh_per_req=0.0479)):
        assert all(x.label in ("MEASURED", "ESTIMATE") for x in rec.actions
                   if isinstance(x.value, (int, float)))
        assert all(x.label in ("MEASURED", "ESTIMATE", "ADVICE") for x in rec.actions)
        assert rec.actions[-1].label == "ADVICE" and "re-run" in rec.actions[-1].text
        text = "\n".join(rec.lines())
        assert "INR" not in text and "\u20b9" not in text and "neutral" not in text.lower()


def test_gate_failed_means_no_change():
    rec = _run(gate_passed=False, final_cap_w=700, final_wh_per_req=0.048)
    assert rec.verdict == R.NO_CHANGE
    assert rec.actions[0].text.startswith(R.NO_CHANGE) and "gate failed" in rec.actions[0].text
    assert "usd_saved" not in _by_key(rec)


def test_tiny_saving_means_no_change():
    rec = _run(final_wh_per_req=0.0475)  # about 1%
    assert rec.verdict == R.NO_CHANGE and "only 1.0%" in rec.actions[0].text


def test_applications_follow_the_numbers():
    yes = _run().applies
    assert any("breaker" in x for x in yes) and any("water-stressed" in x for x in yes)
    no = _run(gate_passed=False, final_cap_w=700).applies
    assert not any("breaker" in x for x in no) and any("EU" in x for x in no)


def test_knob_suggestion():
    tried = _by_key(_run(knob={"knob": "max_num_seqs", "value": 256, "applied": False}))
    assert "try vLLM max_num_seqs=256" in tried["knob"].text and tried["knob"].label == "ESTIMATE"
    kept = _by_key(_run(knob={"knob": "max_num_seqs", "value": 256, "applied": True}))
    assert kept["knob"].text.startswith("keep") and kept["knob"].label == "MEASURED"


def test_spark_knob_ignores_fallback():
    spark = Decision(Choice("knob", knob="enable_prefix_caching", value=True), "r",
                     "muse-spark-1.3")
    fb = Decision(Choice("knob", knob="max_num_seqs", value=128), "r", "fallback")
    gates = [{"knob": "enable_prefix_caching", "passed": True}, {"knob": "max_num_seqs",
                                                                  "passed": True}]
    assert R.spark_knob([spark, fb], gates) == {"knob": "enable_prefix_caching", "value": True,
                                                "applied": True}
    assert R.spark_knob([fb], gates[1:]) is None


def test_deterministic():
    assert _run().to_dict() == _run().to_dict()


def test_summary_fallback_and_numbers_only():
    seen = {}

    def ok(nums):
        seen.update(nums)
        return "  Cap 550 W, 14.6% less energy.  "

    rec = R.with_summary(_run(), ok)
    assert rec.summary_model == "muse-spark-1.3" and rec.summary == "Cap 550 W, 14.6% less energy."
    assert all(isinstance(v, (int, float)) for k, v in seen.items() if k != "verdict")

    def boom(_):
        raise RuntimeError("down")

    rec = R.with_summary(_run(), boom)
    assert rec.summary_model == "rules" and "power_cap_w=550" in rec.summary


def test_impact_recommendations():
    good = R.for_impact(estimate(0.01, 100_000, 8), gpus=8, days=30)
    assert good.verdict == "change recommended"
    bad = R.for_impact(estimate(-0.01, 100_000, 8), gpus=8, days=30)
    assert bad.verdict == R.NO_CHANGE


def test_step_down_emits_recommendations_last(tmp_path):
    got = []
    p99 = lambda cap: 1.0 if cap >= 400 else 1.5  # noqa: E731

    def gate(r):
        return SimpleNamespace(passed=r <= 1.1, failed=[] if r <= 1.1 else ["p99"])

    with Emitter("run_20261027_002000_abc123", root=tmp_path) as em, \
            CapGuard(FakeNVML(), 0, handle_sigterm=False) as g:
        best, _ = step_down(g, start_w=500, floor_w=200, step_w=50, trial=p99, gate=gate,
                            emit=em, baseline=(0.05, 1.0),
                            metrics=lambda r: (0.05 * 0.85, r), on_recommend=got.append)
    assert best == 400 and got[0].verdict == "change recommended"
    lines = (tmp_path / "runs" / "run_20261027_002000_abc123" /
             "events.jsonl").read_text().splitlines()
    last = json.loads(lines[-1])
    assert last["type"] == "recommendations" and last["data"]["actions"][0]["value"] == 400


def test_cli_recommend_example(capsys):
    assert main(["recommend", str(EXAMPLE)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("[EXAMPLE") and "set the power cap to 550 W" in out
    assert main(["recommend", str(EXAMPLE), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "change recommended"


def test_cli_recommend_bad_file(tmp_path):
    with pytest.raises(SystemExit):
        main(["recommend", str(tmp_path / "missing.json")])
