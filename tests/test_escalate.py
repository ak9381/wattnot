from wattnot.route import escalate as E
from wattnot.spark_open.router import attach


def _item(i, kind, s_text, s_ok, b_ok=True, fr=None):
    return {"id": str(i), "kind": kind,
            "small": {"text": s_text, "finish_reason": fr, "correct": s_ok, "joules": 10.0,
                      "usd": 0.0001, "label": "MEASURED"},
            "big": {"correct": b_ok, "joules": 60.0, "usd": 0.002, "label": "ESTIMATE"}}


def test_gate_rules():
    assert E.gate("math", "so #### 42")[0]
    assert not E.gate("math", "it is about forty")[0]
    assert E.gate("code", "```python\ndef f():\n    return 1\n```")[0]
    assert not E.gate("code", "```python\ndef f(:\n```")[0]
    assert not E.gate("qa", "Paris", "length")[0]
    assert not E.gate("qa", "  ")[0]
    assert not E.gate("qa", "Sorry, I can't help")[0]


def test_small_first_beats_big_per_correct():
    items = [_item(0, "math", "#### 4", True), _item(1, "math", "no idea", False),
             _item(2, "code", "```python\nx=1\n```", True), _item(3, "qa", "", False)]
    b = E.evaluate(items)
    pol = {p["policy"]: p for p in b["policies"]}
    sf, bo = pol["small_first"], pol["big_only"]
    assert sf["escalated"] == 2 and sf["correct"] == 4 and bo["correct"] == 4
    assert sf["joules"] == 4 * 10 + 2 * 60
    assert sf["j_per_correct"] < bo["j_per_correct"]
    assert sf["label"] == "ESTIMATE"  # weakest label wins, never upgraded
    assert b["headline"]["j_per_correct_change_pct"] < 0
    assert b["escalation_reasons"] == {"no final answer marker": 1, "empty answer": 1}


def test_no_big_model_means_small_only_and_no_headline():
    it = _item(0, "qa", "Paris", True)
    it.pop("big")
    b = E.evaluate([it])
    assert [p["policy"] for p in b["policies"]] == ["small_only"] and "headline" not in b


def test_zero_correct_gives_none_not_div_zero():
    b = E.evaluate([_item(0, "qa", "x", False, b_ok=False)])
    assert all(p["j_per_correct"] is None for p in b["policies"])


def test_router_gets_small_first_row():
    legs = {n: {"energy_per_answer": {"wh": 1, "label": "MEASURED"},
                "cost_per_1k_answers": {"usd": 1, "label": "MEASURED"},
                "latency_s": {"p99": 1, "label": "MEASURED"},
                "quality": {"pass_pct": 90, "label": "MEASURED"}, "answers": 5, "failed_calls": 0}
            for n in ("local", "baseten")}
    r = {"legs": legs}
    E.attach(r, [_item(0, "math", "nothing", False)])
    attach(r)
    assert r["routing"]["small_first"]["escalated_pct"] == 100.0


def test_verify_chain_and_labels(tmp_path):
    import json
    from pathlib import Path

    from wattnot.cli import main
    from wattnot.verify import labels_step, verify_chain
    d = Path(__file__).resolve().parents[1] / "verifier" / "samples" / "chain"
    docs = [json.loads(p.read_text()) for p in sorted(p for p in d.glob("*.json") if not p.name.endswith(".sigstore.json"))]
    assert verify_chain(docs).ok
    assert not verify_chain(docs[::-1]).ok
    assert labels_step({"result": {"x": {"label": "MEASURED"}}}).ok
    assert not labels_step({"result": {"x": {"label": "SIMULATED"}}}).ok
    assert not labels_step({"a": {"label": "GUESS"}}).ok
    assert main(["verify", *map(str, sorted(d.glob("*.json"))), "--chain"]) == 0


def test_rates_and_net_overhead():
    items = [_item(0, "math", "#### 4", True), _item(1, "math", "no", False)]
    b = E.evaluate(items, overhead_joules=20.0, overhead_label="ESTIMATE")
    assert b["escalation_rate_pct"] == 50.0 and b["small_model_pass_pct"] == 50.0
    sf = next(p for p in b["policies"] if p["policy"] == "small_first")
    assert sf["joules_gross"] == 80.0 and sf["joules"] == 100.0 and sf["label"] == "ESTIMATE"


def test_rekor_online_step():
    import base64
    import json

    from wattnot.verify import rekor_online_step
    r = {"integrity": {"sha256": "ab" * 32}}
    body = base64.b64encode(json.dumps({"spec": {"data": {"hash": {"value": "ab" * 32}}}}).encode()).decode()
    ok = rekor_online_step(r, {"rekor": {"log_index": 5}}, fetch=lambda i: {"u": {"body": body, "integratedTime": 1}})
    assert ok.ok
    assert not rekor_online_step(r, {"rekor": {}}).ok
    bad = rekor_online_step({"integrity": {"sha256": "cd" * 32}}, {"rekor": {"log_index": 5}},
                            fetch=lambda i: {"u": {"body": body}})
    assert not bad.ok
