"""Sequential side by side: headline, receipts, EU report and J per correct use MEASURED NVML
per-arm joules; runs are kept one per run id; agent tool-call turns compare by tool + args."""
import json

from wattnot.gateway import plug
from wattnot.gateway.monitor import agree_turn


def _rec(ts, rid):
    return {"ts": ts, "receipt_id": rid, "status": 200, "actual": {"joules": 100.0, "usd": 0.001, "prompt_tokens": 10,
            "completion_tokens": 10}, "baseline": {"joules": 100.0, "usd": 0.001, "prompt_tokens": 10,
            "completion_tokens": 10}, "labels": {"joules": "ESTIMATE", "usd": "ESTIMATE", "tokens": "MEASURED"}, "stepped_up": False, "guard_stopped": False}


def _setup(tmp_path):
    log = tmp_path / "savings.jsonl"
    log.write_text("".join(json.dumps(_rec(t, f"gw_{i}")) + "\n" for i, t in enumerate([15.0, 16.0, 45.0])))
    runs = [{"run_id": "seq-1-text", "workload": "text",
             "without": {"requests": 2, "joules": 1000.0, "correct": 2, "scored": 2, "j_per_correct": 500.0, "t0": 0, "t1": 10},
             "with": {"requests": 2, "joules": 800.0, "correct": 2, "scored": 2, "j_per_correct": 400.0, "cap_w": 375, "t0": 12, "t1": 20}},
            {"run_id": "seq-2-agent", "workload": "agent",
             "without": {"requests": 1, "joules": 500.0, "correct": None, "scored": 0, "t0": 30, "t1": 40},
             "with": {"requests": 1, "joules": 450.0, "correct": None, "scored": 0, "cap_w": 375, "t0": 42, "t1": 50}}]
    (tmp_path / "sequential.runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in runs))
    return log


def test_feed_headline_receipts_eu_and_quality_are_measured(tmp_path):
    log = _setup(tmp_path)
    f = plug.feed(log)
    e = f["summary"]["energy_wh"]
    assert e["label"].startswith("MEASURED") and round(e["saved_pct"], 1) == round(100 * 250 / 1500, 1)
    assert f["summary"]["energy_wh_estimate"]["label"].startswith("ESTIMATE")
    r = {x["receipt_id"]: x for x in f["receipts"]}
    assert r["gw_0"]["actual"]["joules"] == 400 and r["gw_0"]["baseline"]["joules"] == 500
    assert "allocation" in r["gw_0"]["labels"]["joules"] and r["gw_2"]["run_id"] == "seq-2-agent"
    eu = plug.eu_report(log)
    assert eu["energy_saved_kwh"]["label"].startswith("MEASURED") and abs(eu["energy_saved_kwh"]["value"] - 250 / 3.6e6) < 2e-6
    q = plug.quality(log, {"pairs": 6, "agreement": 0.5})
    assert q["measured"] and q["j_per_correct"] == 400.0 and q["j_per_correct_stock"] == 500.0
    assert q["j_per_request"][0]["workload"] == "agent" and "not scored" in q["j_per_request"][0]["note"]


def test_runs_are_kept_per_run():
    import wattnot.gateway.traffic as tr
    assert "sequential.runs.jsonl" in open(tr.__file__).read()


def test_tool_call_turns_agree_on_same_tool_and_args():
    t = [{"function": {"name": "price", "arguments": '{"item": "a", "n": 2}'}}]
    t2 = [{"function": {"name": "price", "arguments": '{"n": 2, "item": "a"}'}}]
    assert agree_turn("", t, "", t2)
    assert not agree_turn("", t, "", [{"function": {"name": "inventory", "arguments": "{}"}}])
    assert agree_turn("#### 42", None, "so 42", None)
