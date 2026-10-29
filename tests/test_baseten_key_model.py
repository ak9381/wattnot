import pytest
from wattnot.cloud import MODEL, inference_key, JUDGE_MAX_TOKENS
from wattnot.gates.judge import parse_verdicts


def test_model_id():
    assert MODEL == "meta-models/muse-spark-open"


def test_key_order():
    assert inference_key({"BASETEN_METRICS_KEY": "a", "BASETEN_API_KEY": "b"}) == ("BASETEN_METRICS_KEY", "a")
    assert inference_key({"BASETEN_API_KEY": "b"}) == ("BASETEN_API_KEY", "b")
    assert inference_key({}) == ("", "")


def test_cutoff_is_not_a_pass():
    with pytest.raises(ValueError, match="cut off"):
        parse_verdicts({"choices": [{"message": {"content": None}}]}, ["x"])
    assert JUDGE_MAX_TOKENS >= 512
