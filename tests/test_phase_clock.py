from wattnot.control.phase_clock import Controller, MockNVML, classify, counters

M = 'vllm:prompt_tokens_total{{model_name="m"}} {p}\nvllm:generation_tokens_total{{model_name="m"}} {g}\n'


def test_counters_and_classify():
    assert counters(M.format(p=10, g=3)) == (10.0, 3.0)
    assert classify(0, 0) == "idle"
    assert classify(4000, 20) == "prefill"
    assert classify(100, 300) == "decode"


def test_controller_locks_high_for_prefill_low_for_decode_and_resets():
    g = MockNVML(); c = Controller(g, 1980, 1410)
    c.step(M.format(p=0, g=0), 0)
    assert c.step(M.format(p=5000, g=10), 0.1) == "prefill"
    assert c.step(M.format(p=5100, g=400), 0.2) == "decode"
    assert c.step(M.format(p=5200, g=800), 0.3) == "decode"   # no extra switch
    c.close()
    assert g.calls == [("lock", 1980, 1980), ("lock", 1410, 1410), ("reset",)]
    assert c.switches == 2
