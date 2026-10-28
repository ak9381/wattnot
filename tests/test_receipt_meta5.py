"""Tests for receipt extensions in meta-tools-v5: judge fields, wonder traces, spend, and musecode."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wattnot.receipt import build_receipt, verify_hash
from wattnot.receipt.sign import generate_test_key, sign, verify_offline_bundle

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "receipts"


@pytest.fixture
def base_run():
    valid_receipt = json.loads((FIXTURES / "valid.json").read_text())
    return {
        "run_id": "run_20261029_150000_m5test",
        "subject": valid_receipt["subject"],
        "baseline": valid_receipt["baseline"],
        "candidate": valid_receipt["candidate"],
        "measurement": {
            "meter": "NVML internal energy counter via pynvml",
            "hz": 10.0,
            "label": "MEASURED",
            "csv": [{"role": "candidate", "path": "cand.csv", "sha256": "0" * 64}],
        },
        "planner_trace": valid_receipt["planner_trace"],
    }


def test_receipt_includes_judge_and_meta5_fields_and_verifies_hash(base_run):
    events = [
        {"type": "decision", "data": {"choice": {"kind": "cap", "power_cap_w": 550}, "model": "muse-spark-1.3"}},
        {
            "type": "gate.result",
            "data": {
                "check": "judge",
                "passed": True,
                "model": "meta-llama/Llama-3.1-8B-Instruct",
                "agreement": 0.9333,
            },
        },
    ]

    wonder_traces = ["wonder-a1b2c3d4e5f60718"]
    spend = {
        "spark": {"usd": 0.0125, "label": "MEASURED"},
        "wonder": {"usd": 0.0480, "label": "MEASURED"},
        "glimmer": {"usd": 0.0, "label": "MOCK"},
        "llama": {"usd": 0.0, "label": "MOCK"},
        "sam": {"usd": 0.0, "label": "MOCK"},
    }
    patch_hash = "4c424a180373e34b3f3b92eb13a362aa6b0ffc17d7b21235b21111a4cf13a52f"

    receipt = build_receipt(
        base_run,
        events=events,
        wonder_trace_ids=wonder_traces,
        per_model_spend=spend,
        musecode_patch_sha256=patch_hash,
    )

    m = receipt["measurement"]
    assert m["judge_verdict"] == "pass"
    assert m["judge_score"] == 0.9333
    assert m["judge_model"] == "meta-llama/Llama-3.1-8B-Instruct"
    assert m["spark_trace_id"].startswith("spark-")
    assert m["wonder_trace_ids"] == wonder_traces
    assert m["per_model_spend"] == spend
    assert m["musecode_patch_sha256"] == patch_hash

    # Verify hash integrity
    ok, expected, got = verify_hash(receipt)
    assert ok is True
    assert expected == got


def test_tampering_judge_field_invalidates_receipt_hash(base_run):
    receipt = build_receipt(
        base_run,
        judge_verdict="pass",
        judge_score=0.95,
        judge_model="meta-llama/Llama-3.1-8B-Instruct",
    )
    ok, _, _ = verify_hash(receipt)
    assert ok is True

    # Tamper with judge verdict
    receipt["measurement"]["judge_verdict"] = "fail"
    ok_tampered, _, _ = verify_hash(receipt)
    assert ok_tampered is False


def test_signing_receipt_with_judge_fields_verifies(tmp_path, base_run):
    receipt = build_receipt(
        base_run,
        judge_verdict="pass",
        judge_score=0.96,
        judge_model="meta-llama/Llama-3.1-8B-Instruct",
        wonder_trace_ids=["wonder-trace-001"],
    )

    rcpt_file = tmp_path / "receipt.json"
    rcpt_file.write_text(json.dumps(receipt, indent=2))

    key_file = generate_test_key(tmp_path / "test.pem")
    bundle_file = sign(rcpt_file, no_rekor=True, key_path=key_file)

    signed_receipt = json.loads(rcpt_file.read_text())
    bundle = json.loads(bundle_file.read_text())

    ok, signer = verify_offline_bundle(signed_receipt, bundle)
    assert ok is True
    assert signer.startswith("offline:")
