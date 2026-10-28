import pytest

from wattnot.gates import judge
from wattnot.moe.meta_moe import CANDIDATES, DEFAULT, sweep_plan, topk_override_args


def test_meta_default_and_args():
    assert CANDIDATES[DEFAULT]["usable"] and not CANDIDATES["muse-glimmer-30b"]["usable"]
    assert topk_override_args("spark", 8, 8) == []
    assert topk_override_args("spark", 6, 8) == ["--override-kv", "spark.expert_used_count=int:6"]
    with pytest.raises(ValueError):
        topk_override_args("spark", 9, 8)
    assert [a["k"] for a in sweep_plan("spark", 8)] == [8, 6, 4]


def test_judge_defaults_to_llama_local(monkeypatch):
    monkeypatch.setenv("JUDGE_BASE_URL", "http://127.0.0.1:9/v1")
    tr, model = judge.default_transport(judge.LLAMA_JUDGE)
    assert isinstance(tr, judge.LocalJudge) and model == judge.LLAMA_JUDGE
    seen = {}
    res = judge.judge({"a": "1"}, {"a": "1"}, [{"id": "a", "question": "q"}],
                      transport=lambda b: seen.setdefault("b", b) and {
                          "choices": [{"message": {"content": '{"verdicts":[{"id":"a","verdict":"same"}]}'}}]},
                      model=judge.LLAMA_JUDGE)
    assert res.passed and res.detail["judge_era"] == "meta-judge"
