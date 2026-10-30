"""`wattnot report eu` on the signed receipt the power-cap sweep writes (proof-receipt/1).

The fixture is a receipt written by `wattnot.proof.orchestrate --mode rehearsal` (the same
code the live sweep runs, on the fake GPU), so its label is MOCK and the export says
SIMULATED. The adapter refuses a receipt whose SHA-256 does not match.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from wattnot.report.eu import export, fields_for, verify_proof_hash

FIX = Path(__file__).parent / "fixtures" / "proof-receipt-rehearsal.json"


def rows(r):
    return {f["field"]: f for f in fields_for(r)}


def test_real_receipt_shape_exports_with_labels_and_units():
    r = json.loads(FIX.read_text())
    assert r["spec"] == "wattnot.proof-receipt/1" and verify_proof_hash(r)
    f = rows(r)
    assert f["energy_per_request"]["unit"] == "Wh/request"
    assert f["it_energy_measured_window"]["unit"] == "kWh"
    assert f["water_per_request"]["unit"] == "mL/request"
    # MOCK receipts never read MEASURED
    assert all(x["label"] in ("SIMULATED", "ESTIMATE", "NOT_AVAILABLE") for x in f.values())
    assert f["energy_per_request"]["label"] == "SIMULATED"
    best = next(s for s in r["steps"] if s["cap_w"] == r["best_cap_w"])
    j = sum(best["j_per_req_cap"]) / len(best["j_per_req_cap"])
    assert f["energy_per_request"]["value"] == pytest.approx(j / 3600, rel=1e-5)   # J -> Wh
    # Wh x L/kWh = mL
    assert f["water_per_request"]["value"] == pytest.approx(j / 3600 * 0.36, rel=1e-3)
    assert "per-block" in f["energy_saving_billed"]["source"]
    assert f["pue"]["label"] == "NOT_AVAILABLE"


def test_measured_label_carries_through(tmp_path):
    r = json.loads(FIX.read_text())
    r["label"] = "MEASURED"
    from wattnot.receipt.canon import canonical_sha256
    r["integrity"] = {"sha256": None, "signer": None, "rekor": None}
    r["integrity"]["sha256"] = canonical_sha256(r)
    assert rows(r)["energy_per_request"]["label"] == "MEASURED"


def test_tampered_receipt_is_refused(tmp_path):
    r = json.loads(FIX.read_text())
    r["best_cap_w"] = 350
    with pytest.raises(ValueError, match="SHA-256"):
        fields_for(r)


def test_cli_export_writes_csv_and_html(tmp_path):
    c, h = export([FIX], tmp_path / "eu")
    assert "joules_per_correct_answer" in c.read_text() and h.exists()


def test_plan_runs_the_export_right_after_the_sweep(tmp_path):
    from wattnot.longrun.driver import plan_glimmer_test
    ids = [b.id for b in plan_glimmer_test(tmp_path)]
    assert ids.index("t1-eu") == ids.index("t1-sweep") + 2      # after the Wonder chart read
    eu = next(b for b in plan_glimmer_test(tmp_path) if b.id == "t1-eu")
    assert not eu.host and "t1-sweep" in eu.depends_on and "report eu" in eu.live[-1]
