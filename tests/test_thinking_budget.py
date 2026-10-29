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


def test_budget_chat_answers_inside_phase_a():
    # Phase A closes its own thought and answers: no forced close, no phase B.
    calls = []

    def post(body):
        calls.append(body)
        return {"choices": [{"finish_reason": "stop", "message": {"content": "The answer is 42."}}],
                "usage": {"completion_tokens": 20}}
    c = T.BudgetChat("u", "m", 100, post=post)
    r = c([{"role": "user", "content": "q"}], 64)
    assert isinstance(r, Reply) and r.finish == "stop" and "42" in r.text
    assert len(calls) == 1              # phase B never ran
    assert c.forced == {} and c.cut == {}


def test_budget_tokens_clamps_both_ends():
    t = T.build()[0]
    assert T.budget_tokens(t, 0.0) == 16                               # floor
    assert T.budget_tokens(t, 1e9) == T.CONTEXT - T.ANSWER_CAP - 1024  # ceiling fits context
    mid = T.budget_tokens(t, 1.0)
    assert 16 <= mid <= T.CONTEXT - T.ANSWER_CAP - 1024


def test_forced_close_storm_marks_block_suspect():
    # Almost every early item force-closed: the server probably never emitted </think>.
    stormy = [T.Cell("easy", 0.25,
                     [{"id": str(i), "ok": True, "cut": False, "forced": i < 9}
                      for i in range(10)], 100.0)]
    fc = T.forced_close_check(stormy)
    assert fc["forced_close_rate"] > 0.8 and fc["suspect"] is True


def test_forced_close_check_not_suspect_when_rate_is_low():
    calm = [T.Cell("easy", 1.0,
                   [{"id": str(i), "ok": True, "cut": False, "forced": i == 0}
                    for i in range(10)], 100.0)]
    assert T.forced_close_check(calm)["suspect"] is False


def test_fake_run_logs_and_flags_when_forced_close_storms(tmp_path, monkeypatch):
    # Wire the guard end-to-end: force every fake item and confirm run() logs + flags it.
    monkeypatch.setattr(T, "fake_cell", lambda ts, x: [
        {"id": t.id, "ok": True, "cut": False, "forced": True, "tokens": 50, "joules": 60.0}
        for t in ts])
    logs = []
    doc = T.run(tmp_path, fake=True, per_tier=2, log=logs.append)
    assert doc["forced_close_check"]["suspect"] is True
    assert any("suspect" in m for m in logs)


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
