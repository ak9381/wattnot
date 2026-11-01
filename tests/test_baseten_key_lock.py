"""Baseten inference must use only the hackathon key (BASETEN_API_KEY), never BASETEN_METRICS_KEY."""
import pytest

from wattnot.cloud import INFERENCE_KEY_ENVS, inference_key
from wattnot.cloud.transport import LiveTransport, MissingKey


def test_only_hackathon_key_for_inference():
    assert INFERENCE_KEY_ENVS == ("BASETEN_API_KEY",)
    assert inference_key({"BASETEN_METRICS_KEY": "personal", "BASETEN_API_KEY": "hack"}) == ("BASETEN_API_KEY", "hack")
    assert inference_key({"BASETEN_METRICS_KEY": "personal"}) == ("", "")


def test_transport_refuses_metrics_key_alone():
    with pytest.raises(MissingKey):
        LiveTransport(env={"BASETEN_METRICS_KEY": "personal"})
