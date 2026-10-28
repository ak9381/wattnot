from wattnot.context_saver import prefix_cache as PC


def test_hit_rate_from_counter_delta():
    before = "vllm:prefix_cache_queries_total 100\nvllm:prefix_cache_hits_total 10\n"
    after = "vllm:prefix_cache_queries_total 200\nvllm:prefix_cache_hits_total 90\n"
    # delta hits 80 / delta queries 100 = 0.8
    assert PC.hit_rate_from_metrics(before, after) == 0.8


def test_hit_rate_falls_back_to_gauge():
    assert PC.hit_rate_from_metrics("", "vllm:gpu_prefix_cache_hit_rate 0.55\n") == 0.55
    assert PC.hit_rate_from_metrics("", "") is None


def test_mock_ab_on_arm_wins_and_passes_gate():
    ab = PC.mock_ab(reps=3)
    assert ab["lever"] == "enable_prefix_caching" and ab["kept"] is True
    off, on = ab["arms"]["off"], ab["arms"]["on"]
    assert on["cache_hit_rate"]["mean"] > 0.5 and off["cache_hit_rate"]["mean"] == 0.0
    assert ab["headline"]["j_per_correct_change_pct"] < 0   # fewer joules per correct answer
    assert ab["headline"]["ttft_change_pct"] < 0            # faster first token
    assert off["reps"] == 3 and on["reps"] == 3
    assert off["label"] == "MOCK"


def test_gate_fails_when_quality_drops():
    off = PC.arm_summary("off", [{"j_per_req": 11.0, "pass_pct": 96.0, "ttft_ms": 400,
                                   "p99_ms": 3000, "cache_hit_rate": 0.0}], "MEASURED")
    on = PC.arm_summary("on", [{"j_per_req": 7.0, "pass_pct": 94.0, "ttft_ms": 120,
                                 "p99_ms": 2900, "cache_hit_rate": 0.8}], "MEASURED")
    ab = PC.evaluate_ab(off, on)
    assert ab["kept"] is False and any("pass rate" in f for f in ab["gate"]["failed"])


def test_gate_fails_when_p99_rises_too_much():
    off = PC.arm_summary("off", [{"j_per_req": 11.0, "pass_pct": 96.0, "ttft_ms": 400,
                                   "p99_ms": 3000, "cache_hit_rate": 0.0}], "MEASURED")
    on = PC.arm_summary("on", [{"j_per_req": 7.0, "pass_pct": 96.0, "ttft_ms": 120,
                                 "p99_ms": 3400, "cache_hit_rate": 0.8}], "MEASURED")  # +13%
    ab = PC.evaluate_ab(off, on)
    assert ab["kept"] is False and any("p99" in f for f in ab["gate"]["failed"])


def test_run_ab_interleaves_and_aggregates():
    def trial(on, rep):
        return {"j_per_req": 7.0 if on else 11.0, "pass_pct": 96.0,
                "ttft_ms": 120 if on else 400, "p99_ms": 3000,
                "cache_hit_rate": 0.8 if on else 0.0}
    ab = PC.run_ab(trial, reps=3, label="MEASURED")
    assert ab["arms"]["off"]["reps"] == 3 and ab["arms"]["on"]["reps"] == 3
    assert ab["kept"] is True and ab["arms"]["on"]["label"] == "MEASURED"
