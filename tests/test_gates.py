from __future__ import annotations

import json

import pytest

from wattnot.events.emitter import Emitter
from wattnot.gates import Check, decide, fixed_checks, guarded_apply, p99_gate, run_check
from wattnot.runner.trial import TrialResult
from wattnot.runner.vllm_server import TrialParams
from wattnot.runner.workload import WorkloadItem

RUN = "run_20261027_000000_abcdef"


def _trial(tid: str, p99: float) -> TrialResult:
    return TrialResult(tid, TrialParams(), (), 20, 0.01, 1.0, p99 / 2, p99 * 0.9, p99)


def _items() -> list[WorkloadItem]:
    return [
        WorkloadItem("a", "x", 8, {"checks": [{"type": "exact", "value": "42"}]}),
        WorkloadItem("b", "x", 8, {"checks": [{"type": "contains", "value": "def f("}]}),
        WorkloadItem("c", "x", 8, {"checks": [{"type": "regex", "pattern": r"^return \d+$"}]}),
        WorkloadItem("d", "x", 8, {"checks": [{"type": "contains", "value": "ok"}]}),
        WorkloadItem("e", "x", 8),  # unchecked, ignored
    ]


GOOD = {"a": " 42\n", "b": "def f(x):", "c": "y\nreturn 7", "d": "ok", "e": "?"}


def _events(tmp_path):
    path = tmp_path / "runs" / RUN / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_check_kinds():
    assert run_check(Check("exact", "a"), " a ")
    assert not run_check(Check("contains", "zz"), "abc")
    assert run_check(Check("regex", r"^b$"), "a\nb")
    with pytest.raises(ValueError):
        Check.from_meta({"type": "llm"})
    with pytest.raises(ValueError):
        Check.from_meta({"type": "regex"})


def test_fixed_holds_when_quality_same():
    r = fixed_checks(GOOD, dict(GOOD), _items())
    assert r.passed and r.detail["checked"] == 4


def test_fixed_fails_on_drop_and_missing_output():
    bad = dict(GOOD)
    del bad["a"]
    r = fixed_checks(GOOD, bad, _items())
    assert not r.passed
    assert r.detail["candidate_pct"] == 75.0


def test_fixed_allows_drop_inside_tolerance():
    bad = dict(GOOD, d="nope")
    assert fixed_checks(GOOD, bad, _items(), max_drop_pts=25.0).passed


def test_fixed_fails_with_no_checks():
    assert not fixed_checks({}, {}, [WorkloadItem("e", "x", 8)]).passed


def test_p99_slack_and_sla():
    base = _trial("b", 1.0)
    assert p99_gate(base, _trial("c", 1.10)).passed
    assert not p99_gate(base, _trial("c", 1.11)).passed
    assert p99_gate(base, _trial("c", 1.5), sla_s=2.0).passed
    with pytest.raises(ValueError):
        p99_gate(base, base, slack=-1)


def test_pass_applies_and_logs(tmp_path):
    calls = []
    with Emitter(RUN, root=tmp_path) as em:
        d = decide(_trial("b", 1.0), _trial("t1", 1.05), GOOD, GOOD, _items(), emit=em)
        ok = guarded_apply(d, apply=lambda: calls.append("apply"),
                           revert=lambda: calls.append("revert"),
                           change={"power_cap_w": 400}, emit=em)
    assert ok and calls == ["apply"]
    types = [e["type"] for e in _events(tmp_path)]
    assert types == ["gate.result", "gate.result", "action.applied"]


def test_any_fail_reverts(tmp_path):
    calls = []
    with Emitter(RUN, root=tmp_path) as em:
        d = decide(_trial("b", 1.0), _trial("t2", 1.5), GOOD, GOOD, _items(), emit=em)
        ok = guarded_apply(d, apply=lambda: calls.append("apply"),
                           revert=lambda: calls.append("revert"),
                           change={"power_cap_w": 300}, emit=em)
    assert not ok and calls == ["revert"]
    ev = _events(tmp_path)
    assert ev[-1]["type"] == "action.rolled_back"
    assert ev[-1]["data"]["failed_gates"] == ["p99"]
    assert [e["data"]["passed"] for e in ev[:2]] == [True, False]
