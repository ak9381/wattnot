import json

from wattnot.gates.explain import explain, failures_from_events, rule_explain

EV = [{"type": "gate.result", "data": {"trial_no": 325, "check": "p99", "passed": False,
                                       "detail": {"score": 10.95, "threshold": 10.0}}},
      {"type": "gate.result", "data": {"trial_no": 350, "check": "p99", "passed": True}}]
CL = {"math": 50, "code": 29, "qa": 32}


def _reply(items):
    return {"choices": [{"message": {"content": json.dumps({"explanations": items})}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 40}}


def test_only_failures_go_out():
    f = failures_from_events(EV, CL)
    assert len(f) == 1 and f[0]["id"] == "325-p99" and f[0]["classes"] == CL


def test_one_batched_call_and_spark_text_used():
    calls = []

    def tr(body):
        calls.append(body)
        return _reply([{"id": "325-p99", "prompt_class": "code", "reason": "long code answers"}])
    out = explain(failures_from_events(EV, CL), transport=tr)
    assert len(calls) == 1 and out["calls"] == 1 and out["fallback"] is None
    assert out["items"][0]["source"] == "muse-spark" and out["items"][0]["label"] == "ADVICE"
    assert "prompt" not in json.dumps(calls[0]["messages"][1])  # numbers and class counts only


def test_unknown_class_and_failure_fall_back_to_rules():
    out = explain(failures_from_events(EV, CL),
                  transport=lambda b: _reply([{"id": "325-p99", "prompt_class": "poetry", "reason": "x"}]))
    assert out["items"][0]["source"] == "rules" and out["fallback"].startswith("FALLBACK")

    def boom(b):
        raise TimeoutError
    out = explain(failures_from_events(EV, CL), transport=boom)
    assert out["items"][0]["source"] == "rules" and "TimeoutError" in out["fallback"]
    assert explain([], transport=boom)["items"] == []


def test_rules_cover_checks():
    for c in ("p99", "quality", "energy", "odd"):
        r = rule_explain({"id": "x", "check": c, "score": 1, "threshold": 2, "classes": CL})
        assert r["reason"] and r["source"] == "rules"
