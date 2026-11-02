"""Step-up latency guard: a slow low attempt is kept instead of rerun at the thinking tier."""
import json
from pathlib import Path

import pytest

from wattnot.e2e import latency_guard as lg

R04F = Path(__file__).resolve().parent / "fixtures/r04f"


def test_default_and_env(monkeypatch):
    monkeypatch.delenv(lg.ENV, raising=False)
    assert lg.max_low_s() == 30.0
    assert lg.allow_step_up(29.9) and not lg.allow_step_up(39.0)
    monkeypatch.setenv(lg.ENV, "0")
    assert lg.allow_step_up(500.0)  # 0 = guard off
    monkeypatch.setenv(lg.ENV, "junk")
    assert lg.max_low_s() == 30.0


def test_explicit_limit():
    assert lg.allow_step_up(10, limit=5) is False
    assert lg.allow_step_up(None, limit=5) is True


def test_inner_router_skips_slow_step_up(monkeypatch):
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("mfi", root / "scripts/e2e/modal_full_inner.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    calls = []

    def fake_attempt(b, c, arm, ds, iid, obj, think, bud):
        calls.append(think)
        return {"id": iid, "deterministic": False, "wall_s": 45.0 if not think else 80.0}
    monkeypatch.setattr(m, "attempt", fake_attempt)
    monkeypatch.setattr(m, "PUB_BUDGET", {"gsm8k": 512})
    monkeypatch.delenv(lg.ENV, raising=False)
    r = m._run_item(None, [], "with_wattnot", ("gsm8k", "g1", object()))
    assert calls == [False] and r["stepup_skipped"] == "latency_guard" and r["wall_s"] == 45.0
    calls.clear()
    monkeypatch.setenv(lg.ENV, "0")
    r = m._run_item(None, [], "with_wattnot", ("gsm8k", "g1", object()))
    assert calls == [False, True] and r["wall_s"] == 125.0


@pytest.mark.skipif(not (R04F / "with.answers.jsonl").exists(), reason="r04f data not in this checkout")
def test_r04f_replay_brings_p99_within_limit():
    w = [json.loads(x) for x in open(R04F / "with.answers.jsonl")]
    o = [json.loads(x) for x in open(R04F / "without.answers.jsonl")]
    low = {}
    for c in map(json.loads, open(R04F / "with.calls.jsonl")):
        if c["think"] == "budget":
            low[c["item"]] = low.get(c["item"], 0) + c["latency_s"]
    walls = []
    for a in w:
        if len(a["tries"]) > 1 and not lg.allow_step_up(low[f'{a["set"]}:{a["id"]}'], limit=30):
            walls.append(low[f'{a["set"]}:{a["id"]}'])
        else:
            walls.append(a["wall_s"])

    def p99(xs):
        s = sorted(xs)
        return s[min(len(s) - 1, int(round(0.99 * (len(s) - 1))))]
    assert p99(walls) <= 1.10 * p99([a["wall_s"] for a in o])


def _a(i, w, tries=("low",)):
    return {"id": f"i{i}", "set": "s", "wall_s": w, "tries": list(tries)}


def test_verdict_strict_pass():
    wo = [_a(i, 10) for i in range(100)]
    assert lg.latency_verdict(wo, [_a(i, 10.5) for i in range(100)])["verdict"] == "pass"


def test_verdict_note_when_two_outliers_and_p95_ok():
    wo = [_a(i, 10 + i * 0.1) for i in range(100)]
    wi = [_a(i, 10 + i * 0.1) for i in range(97)] + [_a(97, 80), _a(98, 90, ("low", "medium")), _a(99, 95, ("low", "medium"))]
    v = lg.latency_verdict(wo, wi)
    assert v["strict_ok"] is False and v["verdict"] == "pass_with_latency_note"
    assert [x["id"] for x in v["items"]] == ["i99", "i98"] and v["items"][0]["reason"].startswith("step-up")


def test_verdict_fail_when_p95_bad_or_too_many():
    wo = [_a(i, 10) for i in range(100)]
    assert lg.latency_verdict(wo, [_a(i, 12) for i in range(100)])["verdict"] == "fail"
    wi = [_a(i, 10) for i in range(96)] + [_a(96 + k, 99) for k in range(4)]
    assert lg.latency_verdict(wo, wi)["verdict"] == "fail"


@pytest.mark.skipif(not (R04F / "with.answers.jsonl").exists(), reason="fixture missing")
def test_r04f_replay_note_only_if_p95_passes():
    wi = lg.load_answers(R04F / "with.answers.jsonl")
    wo = lg.load_answers(R04F / "without.answers.jsonl")
    v = lg.latency_verdict(wo, wi)
    assert v["strict_ok"] is False
    if v["p95_ok"]:
        assert v["verdict"] == "pass_with_latency_note"
        assert {x["id"] for x in v["items"]} <= {"HumanEval/32", "complex-11"} and v["items"]
    else:
        assert v["verdict"] == "fail"


def test_p99_check_in_stage_verdict(tmp_path):
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("mfr", root / "scripts/e2e/modal_full_round.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    (tmp_path / "raw").mkdir()
    for n in ("with", "without"):
        (tmp_path / "raw" / f"{n}.answers.jsonl").write_text((R04F / f"{n}.answers.jsonl").read_text())
    S = {"without": {"item_latency_s": {"p99": 88.3794}}, "with": {"item_latency_s": {"p99": 104.6458}}}
    c = m.p99_check({"p99_ok": False}, S, tmp_path)
    assert c["strict_ok"] is False and c["verdict"] in ("pass_with_latency_note", "fail")
    assert m.p99_check({"p99_ok": False}, S, None)["ok"] is False


def test_projected_rerun_rule(monkeypatch):
    monkeypatch.delenv(lg.RERUN_ENV, raising=False)
    assert lg.allow_step_up(20, limit=30)  # old rule by default
    assert not lg.allow_step_up(20, limit=30, rerun=1.0)
    assert lg.allow_step_up(14, limit=30, rerun=1.0)
    monkeypatch.setenv(lg.RERUN_ENV, "1")
    assert not lg.allow_step_up(20, limit=30)
    assert lg.allow_step_up(500, limit=0, rerun=1.0)  # guard off


def test_baseten_round_uses_projected_rule(monkeypatch):
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("btr", root / "scripts/e2e/baseten_round.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    efforts = []

    def fake_attempt(b, tr, cap, calls, arm, effort, ds, iid, obj):
        efforts.append(effort)
        return {"id": iid, "deterministic": False, "wall_s": 20.0, "finish": "stop", "answer": "x"}
    monkeypatch.setattr(m, "attempt", fake_attempt)
    monkeypatch.delenv(lg.ENV, raising=False)
    monkeypatch.delenv(lg.RERUN_ENV, raising=False)
    r = m.run_item(None, None, None, None, "with_wattnot", ("ours", "complex-00", object()))
    assert efforts == ["low"] and r["stepup_skipped"] == "latency_guard" and r["wall_s"] == 20.0


def test_baseten_r03_replay():
    d = json.load(open(Path(__file__).resolve().parent / "fixtures/baseten_r03/walls.json"))["items"]
    wo = [a for a in d if a["arm"] == "without_wattnot"]
    w = [a for a in d if a["arm"] == "with_wattnot"]
    stepped = [a for a in w if len(a["tries"]) > 1]
    # every step-up was a complex agent item and none was fixed by the medium rerun (MEASURED)
    assert len(stepped) == 8 and all(a["tier"] == "complex" and not a["deterministic"] for a in stepped)
    assert lg.latency_verdict(wo, w)["verdict"] == "fail"
    replay = [dict(a, wall_s=a["low_s"], tries=["low"]) if len(a["tries"]) > 1
              and not lg.allow_step_up(a["low_s"], limit=30, rerun=1.0) else a for a in w]
    assert all(len(a["tries"]) == 1 for a in replay)
    assert sum(a["deterministic"] for a in replay) == sum(a["deterministic"] for a in w)  # no accuracy lost
    v = lg.latency_verdict(wo, replay)
    assert v["p95_ok"] and v["verdict"] == "pass"
