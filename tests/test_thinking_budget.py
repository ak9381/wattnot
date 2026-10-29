import json

from wattnot.longrun import thinking_budget as T
from wattnot.longrun.driver import plan_glimmer, INR_PER_H
from wattnot.proof.client import Reply


def _cell(tier, x, oks, joules, cut=()):
    return T.Cell(tier, x, [{"id": str(i), "ok": ok, "cut": i in cut, "forced": x < 1}
                            for i, ok in enumerate(oks)], joules)


def test_grid_is_eleven_cells_about_45_min():
    assert T.n_cells() == 11 and 35 <= T.planned_minutes() <= 46


def test_gate_keeps_cheapest_within_one_point():
    base = _cell("easy", 1.0, [True] * 30, 300)
    half = _cell("easy", 0.5, [True] * 30, 200)
    quarter = _cell("easy", 0.25, [True] * 29 + [False], 100)  # -3.3 pts and a regression
    p = T.gate([base, half, quarter])["easy"]
    assert p["budget_x"] == 0.5 and p["saving_pct_vs_1x"] > 30


def test_cut_off_and_regressed_count_as_fails():
    base = _cell("hard", 1.0, [True] * 20, 400)
    cheap = _cell("hard", 0.5, [True] * 20, 100, cut={3})
    p = T.gate([base, cheap])["hard"]
    assert p["decision"] == "no change"
    row = [r for r in p["cells"] if r["budget_x"] == 0.5][0]
    assert row["cut_off"] == 1 and row["regressed"] == 1 and row["gate"] == "fail"


def test_route_rounds_up_when_unsure():
    pol = {"easy": {"budget_x": 0.25}, "medium": {"budget_x": 1.0}}
    r = T.route("what is 2+2", pol, classify=lambda p: ("easy", 0.4))
    assert r["tier"] == "medium" and r["budget_x"] == 1.0
    assert T.route("x", pol, label="easy")["budget_x"] == 0.25
    down = T.route("x", pol, classify=lambda p: 1 / 0)
    assert "router down" in down["via"]


def test_budget_chat_forces_close_and_flags_cut():
    calls = []

    def post(body):
        calls.append(body)
        if "stop" in body:
            return {"choices": [{"finish_reason": "length", "message": {
                "reasoning_content": "hmm", "content": ""}}], "usage": {"completion_tokens": 50}}
        return {"choices": [{"finish_reason": "length", "message": {"content": "ANSWER: 4"}}],
                "usage": {"completion_tokens": 9}}
    c = T.BudgetChat("u", "m", 50, post=post)
    r = c([{"role": "user", "content": "q"}], 64)
    assert isinstance(r, Reply) and r.tokens == 59
    assert c.forced["q"] == 1 and c.cut["q"] == 1
    assert calls[1]["continue_final_message"] and calls[1]["messages"][-1]["role"] == "assistant"
    assert calls[1]["messages"][-1]["content"].endswith(T.THINK_CLOSE + "\n")


def test_fake_block_writes_policy(tmp_path):
    doc = T.run(tmp_path, fake=True)
    d = json.loads((tmp_path / "thinking_policy.json").read_text())
    assert d["label"] == "MOCK" and set(d["tiers"]) == {"easy", "medium", "hard", "complex"}
    assert d["gate"]["max_drop_pts"] == 1.0 and doc["sha256"]


def test_glimmer_plan_has_g6_blocks_and_fits_the_balance(tmp_path):
    bl = plan_glimmer(tmp_path)
    ids = [b.id for b in bl]
    assert ids.index("g6-think") == ids.index("g5-fp8") + 1 and "g6b-prefix" in ids
    assert not any("top" in b.id or "top-k" in b.title.lower() for b in bl)
    h = sum(b.hours for b in bl)
    assert h * INR_PER_H < 9097 and abs(h - 16.67) < 0.01
