"""Fake-backend numbers are SIMULATED everywhere: receipt, grade, EU report, events."""

import json
from pathlib import Path

from wattnot.headline import FakeBackend, safe_headline
from wattnot.receipt.grade import claimed_grade
from wattnot.receipt.sign import generate_test_key
from wattnot.recommend import for_run
from wattnot.report import eu

RUN = Path(__file__).resolve().parents[1] / "web" / "run"


def test_fake_receipt_and_report_say_simulated(tmp_path):
    key = generate_test_key(tmp_path / "k.pem")
    out = safe_headline(FakeBackend(seed=2), root=tmp_path, key_path=key,
                        run_id="run_20261028_120000_fa0002", n_items=6)
    r = json.loads(out.receipt.read_text())
    assert r["measurement"]["label"] == "SIMULATED"
    assert claimed_grade(r, rekor=True) == "SIMULATED"  # even a Rekor entry can't lift it
    labels = {f["label"] for f in eu.fields_for(r)}
    assert "MEASURED" not in labels and "SIMULATED" in labels
    assert "MEASURED" not in json.dumps(r)
    assert "MEASURED" not in out.events.read_text()


def test_simulated_recommendations_never_measured():
    rec = for_run(simulated=True, start_cap_w=700, final_cap_w=550, gate_passed=True,
                  base_wh_per_req=0.048, final_wh_per_req=0.040)
    assert rec.actions and all(a.label != "MEASURED" for a in rec.actions)


def test_demo_streams_have_no_measured_label():
    for f in ("demo-events.jsonl", "demo-events.live.jsonl"):
        assert '"MEASURED"' not in (RUN / f).read_text(), f
