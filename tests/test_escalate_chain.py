from wattnot.route import escalate as E
from wattnot.route.mock_backend import mock_item, mock_items, sample_prompts


def test_mock_item_shape():
    it = mock_item({"id": "x", "kind": "math", "solves_at": 1})
    assert [a["tier"] for a in it["attempts"]] == ["llama-8b", "glimmer-30b", "spark-open"]
    # tier 0 fails the gate, tier 1 passes and is correct
    assert not E.gate("math", it["attempts"][0]["text"])[0]
    assert E.gate("math", it["attempts"][1]["text"])[0]
    assert it["attempts"][1]["correct"] and not it["attempts"][0]["correct"]


def test_chain_answers_at_right_tier_and_sums_energy():
    prompts = [{"id": "a", "kind": "qa", "solves_at": 0},
               {"id": "b", "kind": "math", "solves_at": 1},
               {"id": "c", "kind": "code", "solves_at": 2}]
    b = E.evaluate_chain(mock_items(prompts))
    by_id = {p["id"]: p for p in b["per_item"]}
    assert by_id["a"]["answered_by"] == "llama-8b" and not by_id["a"]["escalated"]
    assert by_id["b"]["answered_by"] == "glimmer-30b" and by_id["b"]["escalated"]
    assert by_id["c"]["answered_by"] == "spark-open" and by_id["c"]["escalated"]
    # per-item trail records a gate verdict for every tier reached
    assert [t["gate_pass"] for t in by_id["b"]["trail"]] == [False, True]
    chain = next(p for p in b["policies"] if p["policy"] == "chain")
    # a: 10, b: 10+28, c: 10+28+70 -> 156 J, all correct
    assert chain["joules"] == 156.0 and chain["correct"] == 3


def test_aggregate_rates_and_per_tier_pass():
    b = E.evaluate_chain(mock_items(sample_prompts(20)))
    # sample_prompts: 14 solved at tier0, 4 at tier1, 2 at tier2 -> escalation 30%, small-model 70%
    assert b["small_model_pass_pct"] == 70.0
    assert b["escalation_rate_pct"] == 30.0
    assert round(b["small_model_pass_pct"] + b["escalation_rate_pct"], 2) == 100.0
    tiers = {t["name"]: t for t in b["tiers"]}
    assert tiers["llama-8b"]["attempted"] == 20 and tiers["llama-8b"]["gate_passes"] == 14
    assert tiers["glimmer-30b"]["attempted"] == 6 and tiers["glimmer-30b"]["answered"] == 4


def test_overhead_is_charged_to_the_chain_and_labels_never_upgrade():
    b = E.evaluate_chain(mock_items(sample_prompts(10)), overhead_joules=15.0,
                         overhead_label="ESTIMATE")
    chain = next(p for p in b["policies"] if p["policy"] == "chain")
    assert chain["joules"] == round(chain["joules_gross"] + 15.0, 3)
    assert chain["label"] == "MOCK"  # weakest of MOCK and ESTIMATE
    assert b["label"] == "MOCK"


def test_leaderboard_row_carries_both_rates():
    b = E.evaluate_chain(mock_items(sample_prompts(20)))
    row = E.chain_leaderboard_row(b, setup="WattNot three-tier chain")
    assert row["escalation_rate_pct"] == 30.0 and row["small_model_pass_pct"] == 70.0
    assert row["j_per_correct"]["mean"] > 0
    assert "Llama-3.1-8B-Instruct" in row["model"] and "Muse-Spark-Open" in row["model"]
    assert row["status"] == "illustration"  # MOCK is not a measured row


def test_attach_chain_sets_escalation_block():
    r = {}
    E.attach_chain(r, mock_items(sample_prompts(5)))
    assert r["escalation"]["spec"] == "wattnot.escalation-chain/1"
    assert len(r["escalation"]["chain"]) == 3
