from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from tests.fixtures.nvml_fake import DEFAULT_POWER_LIMIT_MW, FakeNVML

from wattnot.control import CapGuard
from wattnot.control.planned import Measured, planned_loop
from wattnot.events.emitter import Emitter
from wattnot.planner import Choice, Decision, DecisionMaker, fallback_policy, summarize

FIX = Path(__file__).parent / "fixtures" / "spark"
RUN = "run_20261027_001500_abcdef"
KNOBS = {"max_num_seqs": 256, "enable_prefix_caching": True, "quantization": "none"}


def _load(name):
    return json.loads((FIX / name).read_text())


def _replay(response):
    sent = []

    def transport(body):
        sent.append(body)
        return response
    transport.sent = sent
    return transport


def _summary(**kw):
    base = dict(cap_w=300, min_w=200, max_w=700, step_w=25, base_p99_s=1.2, p99_s=1.0,
                slack=0.1, wh_per_req=0.04, base_wh_per_req=0.048, gates=[], knobs=KNOBS)
    base.update(kw)
    return summarize(**base)


def test_recorded_spark_decision_is_used_and_emitted(tmp_path):
    rec = _load("decisions.json")["headroom"]
    t = _replay(rec["response"])
    with Emitter(RUN, root=tmp_path) as em:
        d = DecisionMaker(t, emit=em).decide(rec["summary"])
    assert d.model.startswith("muse-spark") and d.choice == Choice("cap", power_cap_w=275)
    assert "14%" in d.reason and d.tokens["input"] > 0 and not d.clamped
    assert d.line().startswith("Muse Spark chose 275 W because p99")
    ev = json.loads((tmp_path / "runs" / RUN / "events.jsonl").read_text())
    assert ev["type"] == "decision" and ev["data"]["choice"]["power_cap_w"] == 275


def test_request_carries_only_the_summary():
    rec = _load("decisions.json")["headroom"]
    t = _replay(rec["response"])
    DecisionMaker(t).decide(rec["summary"])
    body = t.sent[0]
    assert body["model"] == "muse-spark-1.3" and body["max_tokens"] <= 600
    assert json.loads(body["messages"][1]["content"]) == rec["summary"]
    assert len(body["messages"][1]["content"]) < 1200


def test_truncated_answers_fall_back():
    for rec in _load("truncated.json").values():
        d = DecisionMaker(_replay(rec["response"])).decide(rec["summary"])
        assert d.model == "fallback"


def test_api_error_falls_back():
    def boom(body):
        raise OSError("network down")
    d = DecisionMaker(boom).decide(_summary())
    assert d.model == "fallback" and d.choice == Choice("cap", power_cap_w=275)


def test_unsafe_answers_are_clamped_or_rejected():
    def answer(content):
        return _replay({"model": "muse-spark-1.3", "usage": {},
                        "choices": [{"message": {"content": json.dumps(content)}}]})
    base = {"knob": None, "value": None, "reason": "go low"}
    d = DecisionMaker(answer({**base, "kind": "cap", "power_cap_w": 50})).decide(_summary())
    assert d.choice.power_cap_w == 275 and d.clamped
    d = DecisionMaker(answer({**base, "kind": "cap", "power_cap_w": 9000})).decide(_summary())
    assert d.choice.power_cap_w == 325 and d.clamped
    d = DecisionMaker(answer({"kind": "knob", "knob": "max_num_seqs", "value": 99999,
                              "power_cap_w": None, "reason": "x"})).decide(_summary())
    assert d.model == "fallback"


def test_fallback_policy_rules():
    assert fallback_policy(_summary()).choice == Choice("cap", power_cap_w=275)
    failed = _summary(gates=[{"cap_w": 275, "passed": False, "failed": ["p99"]}])
    assert fallback_policy(failed).choice.kind == "stop"
    tight = _summary(p99_s=1.3, knobs={**KNOBS, "enable_prefix_caching": False})
    assert fallback_policy(tight).choice.knob == "enable_prefix_caching"
    assert fallback_policy(_summary(p99_s=1.3)).choice.kind == "stop"


def _p99(cap):
    return 1.0 if cap >= 275 else 1.0 + (275 - cap) / 50


def test_planned_loop_reverts_on_gate_fail_and_restores(tmp_path):
    nv = FakeNVML()
    trial = lambda cap, knobs: Measured(_p99(cap), 0.048 * cap / 300)  # noqa: E731

    def gate(m):
        ok = m.p99_s <= 1.32
        return SimpleNamespace(passed=ok, failed=[] if ok else ["p99"])

    with Emitter(RUN, root=tmp_path) as em, CapGuard(nv, 0, handle_sigterm=False) as g:
        g.set_cap_w(300)
        res = planned_loop(g, DecisionMaker(None, emit=em), baseline=Measured(1.0, 0.048),
                           trial=trial, gate=gate, knobs=KNOBS, slack=0.2, emit=em)
        assert res.cap_w == 275 and g.cap_w() == 275
        assert [x["passed"] for x in res.gates] == [True, False]
        assert res.decisions[-1].choice.kind == "stop"
    assert nv.current_limit_mw == DEFAULT_POWER_LIMIT_MW
    types = [json.loads(x)["type"] for x in
             (tmp_path / "runs" / RUN / "events.jsonl").read_text().splitlines()]
    assert types == ["decision", "action.applied", "decision", "action.rolled_back",
                     "decision", "recommendations"]


def test_decision_line_for_fallback():
    d = Decision(Choice("stop"), "p99 headroom only 2%", "fallback")
    assert d.line() == "Fallback policy chose to stop because p99 headroom only 2%"


def _decide(name):
    rec = _load("decisions.json")[name]
    return DecisionMaker(_replay(rec["response"])).decide(rec["summary"]), rec["summary"]


def test_all_recorded_scenarios_parse_as_model_decisions():
    assert set(_load("decisions.json")) == {"headroom", "after_fail", "tight", "start"}
    for name in ("headroom", "after_fail", "tight", "start"):
        d, s = _decide(name)
        assert d.model.startswith("muse-spark"), name
        assert 0 < len(d.reason) <= 200 and not d.clamped
        if d.choice.kind == "cap":
            lo, hi = s["range_w"]
            assert lo <= d.choice.power_cap_w <= hi
            assert abs(d.choice.power_cap_w - s["cap_w"]) <= s["step_w"]
        else:
            assert d.choice.knob in s["allowed_knobs"]


def test_after_fail_does_not_lower_the_cap_again():
    d, s = _decide("after_fail")
    assert d.choice.kind != "cap" or d.choice.power_cap_w >= s["cap_w"]


def test_tight_headroom_does_not_cut_power():
    d, s = _decide("tight")
    assert d.choice.kind != "cap" or d.choice.power_cap_w >= s["cap_w"]
    if d.choice.kind == "knob":
        assert s["knobs"].get(d.choice.knob) != d.choice.value  # an actual change


def test_start_steps_down_one_notch():
    d, s = _decide("start")
    assert d.choice.kind == "cap" and d.choice.power_cap_w == s["cap_w"] - s["step_w"]

