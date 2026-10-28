import json

from wattnot.control import phase_planner as P


def _summary(**kw):
    base = dict(high_cap_mhz=1980, sweep_lows=[1410, 1590], base_j_per_req=11.6,
                p99_budget_ms=4000, decode_share_pct=62.0)
    base.update(kw)
    return P.summarize(**base)


def test_mock_spark_path_is_validated_and_traced():
    traces = []
    t = P.MockSparkTransport(prefill_ratio=4.0, low_mhz=1410)
    out = P.choose(_summary(), transport=t, on_trace=traces.append, clock=lambda: 1.0)
    assert out.source == "spark" and not out.clamped
    assert out.high_mhz == 1980 and out.low_mhz == 1410 and out.prefill_ratio == 4.0
    tr = traces[0]
    assert tr["prompt_sha256"] and len(tr["prompt_sha256"]) == 64
    assert tr["usd"]["label"] == "ESTIMATE" and tr["latency_s"]["label"] == "MEASURED"
    assert tr["fallback"] is False and tr["fallback_reason"] is None


def test_out_of_range_answer_is_clamped():
    t = P.MockSparkTransport(prefill_ratio=99.0, high_mhz=9000, low_mhz=50)
    out = P.choose(_summary(), transport=t)
    assert out.source == "spark" and out.clamped
    assert out.prefill_ratio == P.RATIO_MAX and out.high_mhz == P.CLOCK_MAX
    assert out.low_mhz == P.CLOCK_MIN


def test_low_above_high_is_pulled_down():
    t = P.MockSparkTransport(high_mhz=1500, low_mhz=1800)
    out = P.choose(_summary(high_cap_mhz=1500), transport=t)
    assert out.low_mhz <= out.high_mhz == 1500


def test_no_transport_falls_back_to_sweep():
    traces = []
    out = P.choose(_summary(), transport=None, on_trace=traces.append)
    assert out.source == "sweep" and out.low_mhz == 1410  # lowest sweep candidate
    assert traces[0]["fallback"] and "sweep" in traces[0]["fallback_reason"]


def test_bad_transport_falls_back_with_reason():
    def boom(_body):
        raise TimeoutError("slow")
    out = P.choose(_summary(), transport=boom)
    assert out.source == "sweep"


def test_off_schema_answer_falls_back():
    traces = []

    def junk(_body):
        return {"choices": [{"message": {"content": json.dumps({"nope": 1})}}]}
    out = P.choose(_summary(), transport=junk, on_trace=traces.append)
    assert out.source == "sweep" and "invalid" in traces[0]["fallback_reason"]
