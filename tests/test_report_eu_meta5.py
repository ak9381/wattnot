"""Tests for extended EU efficiency report (src/wattnot/report/eu.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wattnot.report.eu import check_wording, fields_for, generate_report

SAMPLES = Path(__file__).resolve().parents[1] / "verifier" / "samples"
REAL_RECEIPT = json.loads((SAMPLES / "real-measured.json").read_text(encoding="utf-8"))


def test_eu_report_contains_kwh_joules_correct_and_water() -> None:
    rows = fields_for(REAL_RECEIPT)
    by_field = {r["field"]: r for r in rows}

    # IT energy window in kWh
    assert "it_energy_measured_window" in by_field
    assert by_field["it_energy_measured_window"]["unit"] == "kWh"
    assert by_field["it_energy_measured_window"]["label"] in ("MEASURED", "SIMULATED", "ESTIMATE")

    # Joules per correct answer
    assert "joules_per_correct_answer" in by_field
    assert by_field["joules_per_correct_answer"]["unit"] == "J/correct"
    assert by_field["joules_per_correct_answer"]["value"] > 0
    assert by_field["joules_per_correct_answer"]["label"] in ("MEASURED", "SIMULATED", "ESTIMATE")

    # Water consumption estimate
    assert "water_per_request" in by_field
    assert by_field["water_per_request"]["unit"] == "mL/request"
    assert by_field["water_per_request"]["label"] == "ESTIMATE"
    assert "source" in by_field["water_per_request"]


def test_generate_report_one_click_framing_and_disclaimer() -> None:
    # One-click HTML generation
    html_output = generate_report(REAL_RECEIPT, format="html")
    assert isinstance(html_output, str)
    assert "supports EU Energy Efficiency Directive data-center reporting" in html_output
    assert "CSRD" in html_output
    assert "Not a compliance claim" in html_output

    # No banned wording
    banned = check_wording(html_output)
    assert banned == []

    # One-click CSV generation (carries the disclaimer too)
    csv_output = generate_report(REAL_RECEIPT, format="csv")
    assert isinstance(csv_output, str)
    assert "it_energy_measured_window" in csv_output
    assert "joules_per_correct_answer" in csv_output
    assert "Not a compliance claim; facility-level metrics require operator metering." in csv_output

    # One-click dict output
    dict_output = generate_report(REAL_RECEIPT, format="json")
    assert dict_output["framing"] == "supports EU Energy Efficiency Directive data-center reporting"
    assert dict_output["csrd"] == "CSRD roadmap"
    assert dict_output["disclaimer"] == ("Not a compliance claim; facility-level metrics "
                                         "require operator metering.")


def test_generate_report_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="unknown report format"):
        generate_report(REAL_RECEIPT, format="pdf")


def test_fields_for_handles_null_quality_and_labels_assumed_pass_rate() -> None:
    doc = json.loads(json.dumps(REAL_RECEIPT))
    doc["candidate"]["quality"] = None
    doc.pop("joules", None)
    rows = fields_for(doc)
    j = next(r for r in rows if r["field"] == "joules_per_correct_answer")
    # With no pass rate the figure assumes 1.0: it must say so and be an ESTIMATE
    assert j["label"] == "ESTIMATE"
    assert "assumed 1.0" in j["source"]


def test_generate_report_refuses_tampered_receipt() -> None:
    tampered = json.loads(json.dumps(REAL_RECEIPT))
    tampered["result"]["billed_saving_pct"] = 99.9  # modify without updating hash
    with pytest.raises(ValueError, match="integrity hash verification failed"):
        generate_report(tampered)
