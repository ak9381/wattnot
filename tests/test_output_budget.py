import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wattnot import reasoning as RZ
from wattnot.agentwaste.server import tier_body


def test_no_switch_uses_output_budget():
    tiers, desc = RZ.saver_tiers("m", "{% if reasoning %}x{% endif %}")
    assert [t["name"] for t in tiers] == ["out-budget", "full-budget"]
    assert "body_extra" not in tiers[0] and "budget" in desc
    b0 = tier_body({"model": "m", "max_tokens": 2048}, tiers[0])
    assert b0["max_tokens"] == RZ.OUTPUT_BUDGET_TOKENS and "chat_template_kwargs" not in b0
    b1 = tier_body({"model": "m", "max_tokens": 2048}, tiers[1])
    assert b1["max_tokens"] == 2048


def test_switch_detected_keeps_thinking_off():
    tiers, _ = RZ.saver_tiers("m", "{% if enable_thinking is false %}{% endif %}", stepup_max_tokens=1536)
    assert tiers[0]["body_extra"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert tiers[1]["reasoning"] and tiers[1]["max_output_cap"] == 1536


def test_read_chat_template(tmp_path):
    (tmp_path / "tokenizer_config.json").write_text(json.dumps({"chat_template": "hi enable_thinking"}))
    assert RZ.template_thinking_switch(RZ.read_chat_template(tmp_path)) == "enable_thinking"
    assert RZ.read_chat_template(tmp_path / "missing") == ""
    assert RZ.read_chat_template(None) == ""


def test_budget_is_completion_cap_also_for_max_completion_tokens():
    tiers, _ = RZ.saver_tiers("m", "")
    assert tier_body({"model": "m", "max_completion_tokens": 2048}, tiers[0])["max_completion_tokens"] == 448


def test_step_totals():
    import langchain_pass as L
    t = L.step_totals([{"prompt_tokens": 10, "completion_tokens": 5, "wall_s": 1.5, "finish": "length"},
                       {"prompt_tokens": 3, "completion_tokens": None, "wall_s": 0.5, "finish": "stop"}])
    assert t == {"calls": 2, "prompt_tokens": 13, "completion_tokens": 5, "reasoning_tokens": 0,
                 "wall_s": 2.0, "finish_length": 1}


def test_fake_pass_writes_totals_and_sampling(tmp_path):
    import langchain_pass as L
    L.main(["--out", str(tmp_path), "--n", "2", "--fake", "--route", "final-check"])
    res = json.loads((tmp_path / "langchain_pass.json").read_text())
    assert res["sampling"] == {"temperature": 0, "seed": 1234}
    assert res["tiers"] == ["out-budget", "full-budget"]
    for arm in ("saver_off", "saver_on"):
        assert res["arms"][arm]["step_totals"]["calls"] >= 1
