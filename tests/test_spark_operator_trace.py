"""Muse Spark operator trace: four calls, each an event with trace id, latency and $."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from wattnot.meta import spark_trace as st

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas" / "run-event.schema.json").read_text())


class FakeLedger:
    def __init__(self):
        self.rows = []

    def entries(self):
        return self.rows


class FakeClient:
    """Stands in for the guarded client: records each call and its priced ledger row."""

    def __init__(self, verdict="PASS", fail_step=None):
        self.ledger, self.bodies, self.verdict, self.fail = FakeLedger(), [], verdict, fail_step

    def __call__(self, body, *, purpose):
        step = body["metadata"]["step"]
        if step == self.fail:
            raise RuntimeError("HTTP 500")
        self.bodies.append(body)
        self.ledger.rows.append({"purpose": purpose, "usd": 0.001})
        ans = {"gate": {"verdict": self.verdict, "why": "x"}}.get(step, {"summary": "s"})
        return {"id": f"resp_{step}", "choices": [{"message": {"content": json.dumps(ans)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


def test_four_live_style_calls_become_valid_events(tmp_path, monkeypatch):
    monkeypatch.delenv("WATTNOT_META_MOCK", raising=False)
    ev = tmp_path / "events.jsonl"
    res = st.run_trace({"probe": {"nvml": {"power_w": 300}}}, events=ev, client=FakeClient(),
                       fixed_gate="PASS")
    assert [c["step"] for c in res["calls"]] == ["probe", "proposal", "gate", "receipt"]
    assert all(c["trace_id"] == f"resp_{c['step']}" for c in res["calls"])
    assert all(c["latency_ms"] >= 0 and c["latency_label"] == "MEASURED" for c in res["calls"])
    assert all(c["usd"] == 0.001 and c["usd_label"] == "ESTIMATE" for c in res["calls"])
    assert res["calls"][2]["agrees_with_fixed_gate"] is True and res["total_usd"] == 0.004
    lines = [json.loads(x) for x in ev.read_text().splitlines()]
    assert len(lines) == 8 and [x["seq"] for x in lines] == list(range(8))
    for x in lines:
        jsonschema.validate(x, SCHEMA)
    done = [x for x in lines if x["type"] == "step.finished"]
    assert all(x["data"]["kind"] == "spark_call" for x in done)


def test_spark_disagreeing_is_recorded_not_obeyed(tmp_path, monkeypatch):
    monkeypatch.delenv("WATTNOT_META_MOCK", raising=False)
    res = st.run_trace({}, events=tmp_path / "e.jsonl", client=FakeClient(verdict="FAIL"),
                       fixed_gate="PASS")
    g = res["calls"][2]
    assert g["agrees_with_fixed_gate"] is False and g["fixed_gate"] == "PASS"


def test_failed_call_is_shown_never_faked(tmp_path, monkeypatch):
    monkeypatch.delenv("WATTNOT_META_MOCK", raising=False)
    res = st.run_trace({}, events=tmp_path / "e.jsonl", client=FakeClient(fail_step="proposal"))
    p = res["calls"][1]
    assert p["status"] == "failed" and "HTTP 500" in p["error"] and not res["ok"]


def test_mock_mode_is_labelled_mock_and_stays_out_of_the_real_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("WATTNOT_META_MOCK", "1")
    real = tmp_path / "real-ledger.json"
    monkeypatch.setenv("WATTNOT_META_LEDGER", str(real))
    res = st.run_trace({}, events=tmp_path / "e.jsonl")
    assert res["ok"] and all(c["label"] == "MOCK" and c["usd_label"] == "MOCK"
                             for c in res["calls"])
    assert not real.exists()


def test_cli_reads_a_flow_folder_and_writes_trace(tmp_path, monkeypatch):
    monkeypatch.setenv("WATTNOT_META_MOCK", "1")
    rc = st.main(["--flow-out", str(tmp_path), "--receipt",
                  str(ROOT / "receipts/rerun-1031/rcpt_20261031_rerun_cap325.json")])
    t = json.loads((tmp_path / "spark/trace.json").read_text())
    assert rc == 0 and len(t["calls"]) == 4 and (tmp_path / "spark/events.jsonl").exists()


def test_inputs_carry_the_receipt_sci_and_gate():
    inp, fixed = st.inputs_from_flow(Path("/nonexistent"), ROOT / "tests/fixtures/receipts/valid.json")
    assert "sci" in inp["receipt"] and fixed in ("PASS", "FAIL")


def test_flow_routes_the_four_steps_through_spark():
    src = (ROOT / "src/wattnot/flow.py").read_text()
    assert '"wattnot.meta.spark_trace"' in src and '"spark-trace"' in src
