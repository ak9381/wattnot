import json

from wattnot.longrun import harness
from wattnot.longrun.grade import World, Trace, last_number, run_tests, same_number
from wattnot.longrun.tasks import TIERS, build, samples_for_ui
from wattnot.proof.client import Reply
from wattnot.proof.fake import FakeNvml


def test_build_is_deterministic_and_tiered():
    a, b = build(), build()
    assert [t.as_dict() for t in a] == [t.as_dict() for t in b]
    assert {t.tier for t in a} == set(TIERS)
    assert len({t.id for t in a}) == len(a)


def test_no_private_sources_in_prompts():
    text = json.dumps([t.as_dict() for t in build()]).lower()
    for bad in ("schema", "ingest", "customer"):
        assert bad not in text


def test_number_grading():
    assert same_number(last_number("blah ANSWER: 1,204", "ANSWER:"), 1204)
    assert not same_number(last_number("no number"), 3)
    assert same_number(12.0, "12")


def test_code_grader_runs_isolated_with_timeout():
    assert run_tests("def f(x):\n    return x+1", ["f(1) == 2"])
    assert not run_tests("def f(x):\n    return x", ["f(1) == 2"])
    assert not run_tests("while True: pass", ["True"], timeout_s=1)


def test_flaky_tool_fails_once_then_works():
    w = World({"price": {"a": 5}, "inventory": {}, "fx": {}, "flaky": ["price"]})
    tr = Trace()
    assert str(w.call("price", {"item": "a"}, tr)).startswith("ERROR 503")
    assert w.call("price", {"item": "a"}, tr) == 5 and tr.errors == 1


def test_calc_rejects_code():
    tr = Trace()
    assert str(World({}).call("calc", {"expr": "__import__('os')"}, tr)).startswith("ERROR")
    assert World({}).call("calc", {"expr": "2*(3+4)"}, tr) == 14


def _block(cap):
    tasks = build()
    n = FakeNvml()
    h = n.device_handle(0)
    return harness.run_block(n, h, harness.FakeChat(n, h, tasks), tasks, cap, log=lambda m: None)


def test_oracle_passes_everything_at_a_mild_cap():
    s = harness.summarize(_block(375), mock=True)
    assert s["label"] == "MOCK"
    for r in s["tiers"]:
        assert r["stock"]["pass_pct"] == 100.0 and r["cap"]["wh_per_task"] > 0
        assert r["energy_change_pct"] < 0
    cx = next(r for r in s["tiers"] if r["tier"] == "complex")
    assert cx["gate"] == "pass"


def test_complex_tier_blocks_when_agents_slip():
    s = harness.summarize(_block(300), mock=True)
    cx = next(r for r in s["tiers"] if r["tier"] == "complex")
    assert cx["gate"] == "blocked" and cx["pass_drop_pts"] > 2


def test_agent_trace_shows_retry_and_subagent():
    b = _block(375)
    details = b["arms"][0]["tiers"]["complex"]["agent"]
    assert any(d["subagents"] for d in details) and all(d["retries"] >= 1 for d in details)


def test_client_error_counts_as_fail():
    class Down:
        def __call__(self, m, k):
            return Reply(0.1, 0, "", "error")
    from wattnot.longrun.grade import grade_one
    assert not any(grade_one(t, Down()).ok for t in build()[::10])


def test_ui_samples_have_no_answers():
    s = samples_for_ui()
    assert [t["tier"] for t in s["tiers"]] == list(TIERS)
    for t in s["tiers"]:
        for x in t["samples"]:
            assert set(x) == {"id", "prompt", "tools", "subagent"}
