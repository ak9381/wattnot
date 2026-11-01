"""Fixes from the second end-to-end review (1 Nov)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_agentwaste_server import make_client  # noqa: E402
from test_api_receipts import S, _client  # noqa: E402


def test_eu_api_refuses_tampered_receipt(tmp_path):
    c = _client(tmp_path)
    r = json.loads((S / "real-measured.json").read_text())
    rid = r["receipt_id"]
    p = tmp_path / "receipts" / f"{rid}.json"
    t = json.loads(p.read_text())
    t["candidate"]["wh_per_req"]["mean"] = 0.0001
    p.write_text(json.dumps(t))
    resp = c.get(f"/api/receipts/{rid}/report/eu")
    assert resp.status_code == 409 and resp.json()["code"] == "hash_mismatch"


def test_upstream_error_keeps_a_call_record():
    client, fake = make_client()
    fake.fail = True
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    assert client.post("/v1/chat/completions", json=body,
                       headers={"X-WattNot-Run": "e1"}).status_code == 502
    rc = client.get("/v1/wattnot/receipt").json()
    gate = rc["gate"]
    assert gate["upstream_errors"] == 1 and gate["stopped_by_guard"] == 0
    assert gate["answered_calls"] == 0


def test_stats_count_second_layer_flags():
    from wattnot.agentwaste import server
    for k in ("error_storm", "low_gain", "paraphrase"):
        assert k in server._FLAG_KINDS


def test_recommend_never_says_measured_for_example(tmp_path, capsys):
    from wattnot import cli
    rep = tmp_path / "r.json"
    rep.write_text(json.dumps({"label": "EXAMPLE", "start_cap_w": 700, "final_cap_w": 350,
                               "gate_passed": True, "base_wh_per_req": 1.0,
                               "final_wh_per_req": 0.7, "base_p99_ms": 100, "final_p99_ms": 105}))
    cli.main(["recommend", str(rep)])
    assert "MEASURED" not in capsys.readouterr().out


def test_failed_summary_keeps_original_text():
    from wattnot.context_saver.middleware import SparkSummarizer

    def boom(*a, **k):
        raise RuntimeError("down")
    s = SparkSummarizer(client=boom)
    assert s("First sentence. Second sentence with id 42.") == \
        "First sentence. Second sentence with id 42."
    assert s.failures == 1


def test_standalone_verifier_lowers_grade_on_weak_headline():
    sys.path.insert(0, str(ROOT / "verifier"))
    import wattnot_verify as wv
    src = (ROOT / "verifier" / "wattnot_verify.py").read_text()
    assert 'g = "UNVERIFIABLE" if lab.get("unknown") else "SIMULATED"' in src
    assert wv.labels_step({"result": {"label": "MOCK"}})["ok"] is False
    both = wv.labels_step({"result": {"label": "MOCK"}, "x": {"label": "WEIRD"}})
    assert both["unknown"] and "headline" in both["error"]
