from pathlib import Path

from wattnot.dash import judgepath

ROOT = Path(__file__).resolve().parents[1]


def test_bundle_keeps_eras_apart_and_labels():
    d = judgepath.build(ROOT)
    assert d["run2"]["era"] == "RUN2" and d["meta_judge"]["label"] == "PENDING"
    assert d["stack"]["energy_saved_pct"]["label"] == "ESTIMATE"
    assert d["escalation"]["preview"]["label"] == "MOCK"
    assert d["verify"]["chain_ok"] is True
    assert len(d["trace"]["calls"]) == 4
