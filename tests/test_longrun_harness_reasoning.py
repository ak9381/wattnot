"""Reasoning-model token budget + truncation reporting in the tiers harness (RUN3 fix)."""

from __future__ import annotations

from wattnot.longrun.harness import Chat, apply_model, model_config, summarize, table
from wattnot.longrun.tasks import TIERS, build


def test_reasoning_floor_gives_small_tasks_real_room():
    cfg = model_config("muse-glimmer-30b")
    tasks = apply_model(build(), cfg)
    # even the smallest easy task (16 tokens x8 = 128) is floored so reasoning + answer fit
    assert min(t.max_tokens for t in tasks) >= cfg["reasoning_floor"] == 1024


def test_non_reasoning_model_is_not_floored():
    cfg = model_config("llama-3.1-8b")
    tasks = apply_model(build(), cfg)
    assert min(t.max_tokens for t in tasks) < 1024   # no floor applied


def test_chat_passes_reasoning_cap_when_configured():
    c = Chat("http://x/v1/chat/completions", "m", chat_template_kwargs={"reasoning_effort": "low"})
    assert c.chat_template_kwargs == {"reasoning_effort": "low"}
    plain = Chat("http://x/v1/chat/completions", "m")
    assert plain.chat_template_kwargs is None


def _block_with_truncation():
    # all tiers, two arms (stock, cap). Only the easy 'cap' arm truncated one of its two items.
    def tier(trunc):
        return {"n": 2, "pass": 1, "joules": 100.0, "wall_s": 1.0, "p99_s": 0.5,
                "tokens": 20, "truncated": trunc, "fails": [], "agent": None}
    stock = {t: tier(0) for t in TIERS}
    cap = {t: tier(1 if t == "easy" else 0) for t in TIERS}
    arms = [{"arm": "stock", "limit_w": 700, "tiers": stock},
            {"arm": "cap", "limit_w": 350, "tiers": cap}]
    return {"cap_w": 350, "stock_w": 700, "arms": arms}


def test_summary_reports_truncation_rate():
    summ = summarize(_block_with_truncation(), mock=True)
    assert summ["truncation"]["items"] == 1 and summ["truncation"]["checked"] == 4 * len(TIERS)
    easy = next(r for r in summ["tiers"] if r["tier"] == "easy")
    assert easy["truncated"] == 1 and easy["truncation_pct"] == 25.0
    assert "cut short" in table(summ)
