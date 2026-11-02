import importlib.util, json
from pathlib import Path

spec = importlib.util.spec_from_file_location("br", Path(__file__).parents[1] / "scripts/e2e/baseten_round.py")
br = importlib.util.module_from_spec(spec); spec.loader.exec_module(br)


def test_own_usage_counts_every_ledger_call(tmp_path):
    led = tmp_path / "baseten_spend.json"
    led.write_text(json.dumps({"usd": [0.001] * 71, "cap_usd": 1.0}))
    rows = [{"prompt_tokens": 200, "cached_tokens": 0, "completion_tokens": 400}] * 12
    own = br.own_usage(rows, 0, br._ledger_n(led), 0.1648)
    assert own["requests"] == 71 and own["arm_only"]["requests"] == 12
    assert own["input_tokens"] is None and own["arm_only"]["input_tokens"] == 2400


def test_ledger_delta_ignores_prior_spend(tmp_path):
    assert br._ledger_n(tmp_path / "missing.json") == 0
    assert br.own_usage([], 5, 9, 0.0)["requests"] == 4


def test_usage_read_after_feature_stages():
    src = (Path(__file__).parents[1] / "scripts/e2e/baseten_round.py").read_text()
    assert src.index("provider_usage(started") > src.index("post_round(raw, doc")
