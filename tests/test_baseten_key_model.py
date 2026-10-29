import pytest
from wattnot.cloud import MODEL, inference_key, JUDGE_MAX_TOKENS
from wattnot.gates import judge as judge_mod
from wattnot.gates.judge import parse_verdicts, request_body


def test_model_id():
    assert MODEL == "meta-models/muse-spark-open"


def test_key_order():
    assert inference_key({"BASETEN_METRICS_KEY": "a", "BASETEN_API_KEY": "b"}) == ("BASETEN_METRICS_KEY", "a")
    assert inference_key({"BASETEN_API_KEY": "b"}) == ("BASETEN_API_KEY", "b")
    assert inference_key({}) == ("", "")


def test_cutoff_is_not_a_pass():
    with pytest.raises(ValueError, match="cut off"):
        parse_verdicts({"choices": [{"message": {"content": None}}]}, ["x"])


def test_judge_max_tokens_is_pinned_to_one_value():
    # cloud, the batch judge and record_judge (via request_body) must all agree, and
    # on the larger 1500 budget (512 was shown to truncate the verdict).
    assert JUDGE_MAX_TOKENS == 1500
    assert judge_mod.JUDGE_MAX_TOKENS == JUDGE_MAX_TOKENS
    assert request_body([{"id": "a", "question": "q", "baseline": "b", "candidate": "c"}])["max_tokens"] == JUDGE_MAX_TOKENS
