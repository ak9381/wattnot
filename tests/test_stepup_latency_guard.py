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
