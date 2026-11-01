"""Baseten inference uses only the paid workspace key (BASETEN_METRICS_KEY), with no fallback."""
import pytest

from wattnot.cloud import INFERENCE_KEY_ENVS, MODEL, inference_key
from wattnot.cloud.transport import LiveTransport, MissingKey


def test_only_paid_key_for_inference():
    assert INFERENCE_KEY_ENVS == ("BASETEN_METRICS_KEY",)
    assert inference_key({"BASETEN_METRICS_KEY": "paid", "BASETEN_API_KEY": "hack"}) == ("BASETEN_METRICS_KEY", "paid")
    assert inference_key({"BASETEN_API_KEY": "hack"}) == ("", "")


def test_transport_refuses_hackathon_key_alone():
    with pytest.raises(MissingKey):
        LiveTransport(env={"BASETEN_API_KEY": "hack"})


def test_model_id():
    assert MODEL == "meta-models/muse-spark-open"


def test_feature_table_has_every_hosted_feature_on():
    from wattnot.e2e.fullstack import feature_table
    t = {f["id"]: f for f in feature_table()}
    for k in ("effort_routing", "context_saver", "waste_guard", "code_mode", "eye_check", "sam_hotspot",
              "hearing_check", "wonder_planner", "spark_trace", "approvals", "musecode_pr", "receipt",
              "eu_report", "spark_grades", "spend_guard"):
        assert t[k]["status"] == "ON"
    for k in ("power_caps", "fp8", "kv_batch", "measured_energy"):
        assert t[k]["status"] == "OFF" and t[k]["reason"]
