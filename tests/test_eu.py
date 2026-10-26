"""T35: EU 2024/1364 field export from receipts."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from wattnot.report.eu import BANNED, LABELS, check_wording, export, fields_for

S = Path(__file__).resolve().parents[1] / "verifier" / "samples"
R = json.loads((S / "real-measured.json").read_text())


def _by(rows):
    return {r["field"]: r for r in rows}


def test_every_field_has_value_unit_label_source():
    for r in fields_for(R):
        assert set(r) >= {"field", "value", "unit", "label", "source"}
        assert r["label"] in LABELS and r["source"]
        assert (r["value"] is None) == (r["label"] == "NOT_AVAILABLE")


def test_mapping_from_fixture():
    f = _by(fields_for(R))
    assert f["energy_per_request"]["value"] == R["candidate"]["wh_per_req"]["mean"]
    assert f["energy_per_request"]["label"] == "MEASURED"
    assert f["water_per_request"]["label"] == "ESTIMATE"
    assert f["energy_saving_billed"]["value"] == R["result"]["billed_saving_pct"]
    want = sum(R["candidate"]["wh_per_req"]["samples"]) * 200 / 1000
    assert abs(f["it_energy_measured_window"]["value"] - want) < 1e-6


def test_facility_fields_not_available():
    f = _by(fields_for(R))
    for k in ("pue", "renewable_energy_share", "waste_heat_reused", "idle_power",
              "sci_per_request"):
        assert f[k]["label"] == "NOT_AVAILABLE" and f[k]["source"]


def test_sci_needs_a_sourced_intensity():
    f = _by(fields_for(R, grid_gco2_per_kwh=400, grid_source="example grid figure"))
    assert f["sci_per_request"]["label"] == "ESTIMATE"
    assert f["sci_per_request"]["value"] == round(R["candidate"]["wh_per_req"]["mean"] / 1000
                                                  * 400, 6)


def test_estimate_receipt_never_shows_measured():
    e = json.loads((S / "real-estimate.json").read_text())
    assert all(r["label"] != "MEASURED" for r in fields_for(e))


def test_noise_receipt_saving_is_zero_and_estimate():
    n = json.loads((S / "real-noise.json").read_text())
    f = _by(fields_for(n))["energy_saving_billed"]
    assert f["value"] == 0 and f["label"] == "ESTIMATE"


def test_export_csv_html_and_banned_words(tmp_path):
    c, h = export(sorted(S.glob("real-*[!e].json")) or [S / "real-measured.json"],
                  tmp_path / "eu-2024-1364")
    text = c.read_text() + h.read_text()
    assert check_wording(text) == []
    rows = list(csv.DictReader(io.StringIO(c.read_text())))
    assert rows and set(rows[0]) == {"receipt_id", "field", "value", "unit", "label", "source"}
    assert "<table>" in h.read_text()
    assert check_wording("we are carbon neutral via offsets") != []
    assert "neutral" in BANNED and "offset" in BANNED


def test_cli_report_eu(tmp_path, capsys):
    from wattnot.cli import main
    assert main(["report", "eu", "--receipts", str(S / "real-measured.json"),
                 "--out", str(tmp_path / "eu")]) == 0
    assert (tmp_path / "eu.csv").exists() and (tmp_path / "eu.html").exists()
