"""Reasoning-token share flows into sweep trials, harness tier summaries and prefix blocks (E)."""

from __future__ import annotations

from wattnot import reasoning


def test_token_share_measured_vs_estimate():
    d = {"usage": {"completion_tokens": 100,
                   "completion_tokens_details": {"reasoning_tokens": 80}}}
    rt, measured = reasoning.reasoning_tokens(d)
    assert (rt, measured) == (80, True)
    assert reasoning.token_share(80, 100, True) == {
        "reasoning_tokens": 80, "completion_tokens": 100, "share": 0.8, "label": "MEASURED"}
    # no usage: estimate from the reasoning text (~4 chars/token), labelled ESTIMATE
    d2 = {"choices": [{"message": {"content": "5", "reasoning_content": "x" * 40}}]}
    assert reasoning.reasoning_tokens(d2) == (10, False)
    assert reasoning.token_share(10, 20, False)["label"] == "ESTIMATE"
    assert reasoning.token_share(0, 0, True)["share"] is None   # no tokens -> no made-up 0


def test_proof_step_summary_carries_reasoning_share():
    from wattnot.proof.session import _reasoning_share
    blocks = [{"reasoning_tokens": 300, "tokens": 500, "reasoning_measured": True},
              {"reasoning_tokens": 100, "tokens": 500, "reasoning_measured": True}]
    r = _reasoning_share(blocks)
    assert r == {"reasoning_tokens": 400, "completion_tokens": 1000, "share": 0.4,
                 "label": "MEASURED"}
    # one estimated block drops the whole step to ESTIMATE
    blocks[1]["reasoning_measured"] = False
    assert _reasoning_share(blocks)["label"] == "ESTIMATE"


def test_sweep_to_trials_adds_reasoning_token_share():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "s2t", Path(__file__).resolve().parents[1] / "scripts" / "sweep_to_trials.py")
    s2t = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(s2t)
    results = {"steps": [{"cap_w": 350, "j_per_req_cap": [10.0, 12.0],
                          "quality": {"cap_pct": 80.0}, "accepted": True, "label": "MEASURED",
                          "reasoning": {"share": 0.42, "label": "MEASURED"}}]}
    t = s2t.trials_from(results)[0]
    assert t["reasoning_token_share"] == 0.42 and t["reasoning_token_label"] == "MEASURED"


def _tier(reasoning_tokens, tokens, measured, n=4, passed=4):
    return {"n": n, "pass": passed, "joules": 100.0, "wall_s": 1.0, "p99_s": 0.5, "tokens": tokens,
            "reasoning_tokens": reasoning_tokens, "reasoning_measured": measured, "truncated": 0,
            "fails": [], "agent": None}


def test_harness_summary_reports_reasoning_share():
    from wattnot.longrun.harness import summarize
    from wattnot.longrun.tasks import TIERS
    arms = []
    for arm in ("stock", "cap"):
        arms.append({"arm": arm, "limit_w": 700 if arm == "stock" else 350,
                     "tiers": {t: _tier(60, 100, True) for t in TIERS}})
    summ = summarize({"cap_w": 350, "arms": arms})
    assert summ["reasoning"]["share"] == 0.6 and summ["reasoning"]["label"] == "MEASURED"
    assert all(r["reasoning"]["share"] == 0.6 for r in summ["tiers"])


def test_prefix_arm_summary_aggregates_reasoning_share():
    from wattnot.context_saver import prefix_cache as PC
    rows = [{"j_per_req": 7.0, "pass_pct": 96.0, "ttft_ms": 120, "p99_ms": 3000,
             "cache_hit_rate": 0.8, "reasoning_token_share": 0.5, "reasoning_measured": True},
            {"j_per_req": 7.2, "pass_pct": 96.0, "ttft_ms": 130, "p99_ms": 3000,
             "cache_hit_rate": 0.8, "reasoning_token_share": 0.6, "reasoning_measured": True}]
    arm = PC.arm_summary("on", rows, "MEASURED")
    assert arm["reasoning_token_share"]["mean"] == 0.55 and arm["reasoning_label"] == "MEASURED"
    ab = PC.mock_ab(reps=3)
    assert ab["headline"]["reasoning_token_share"]["on"] == 0.58
