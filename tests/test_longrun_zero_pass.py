import json
from pathlib import Path

import pytest

from wattnot.longrun.driver import Driver, Block


@pytest.fixture
def driver(tmp_path):
    """Create a minimal Driver instance for testing _all_failed."""
    blocks = []
    vm = type('MockVm', (), {'alive': lambda: True, 'delete': lambda: None})()
    return Driver(tmp_path, blocks, vm, label="TEST", run=lambda b: 0,
                  max_hours=1.0, balance_inr=lambda: 10000, hours_used=lambda: 0)


def test_flags_zero_eye_test(driver, tmp_path):
    """Test that _all_failed flags eye_test.json with 0/10 passed."""
    block_id = "test-block"
    block_dir = tmp_path / "runs" / block_id
    block_dir.mkdir(parents=True)

    eye_test = {
        "detail": {
            "checked": 10,
            "passed_count": 0,
        }
    }
    (block_dir / "eye_test.json").write_text(json.dumps(eye_test))

    reason = driver._all_failed(block_id)
    assert reason is not None
    assert "0/10" in reason
    assert "eye_test" in reason


def test_flags_all_zero_tiers(driver, tmp_path):
    """Test that _all_failed flags tiers_summary.json with all tiers at 0%."""
    block_id = "test-block"
    block_dir = tmp_path / "runs" / block_id
    block_dir.mkdir(parents=True)

    tiers_summary = {
        "tiers": [
            {"tier": "easy", "stock": {"pass_pct": 0.0}, "cap": {"pass_pct": 0.0}},
            {"tier": "medium", "stock": {"pass_pct": 0.0}, "cap": {"pass_pct": 0.0}},
            {"tier": "hard", "stock": {"pass_pct": 0.0}, "cap": {"pass_pct": 0.0}},
        ]
    }
    (block_dir / "tiers_summary.json").write_text(json.dumps(tiers_summary))

    reason = driver._all_failed(block_id)
    assert reason is not None
    assert "0%" in reason
    assert "tiers" in reason


def test_does_not_flag_partial_pass_eye(driver, tmp_path):
    """Test that _all_failed does not flag eye_test.json with partial passes."""
    block_id = "test-block"
    block_dir = tmp_path / "runs" / block_id
    block_dir.mkdir(parents=True)

    eye_test = {
        "detail": {
            "checked": 10,
            "passed_count": 7,
        }
    }
    (block_dir / "eye_test.json").write_text(json.dumps(eye_test))

    reason = driver._all_failed(block_id)
    assert reason is None


def test_does_not_flag_partial_pass_tiers(driver, tmp_path):
    """Test that _all_failed does not flag tiers_summary.json with some tiers passing."""
    block_id = "test-block"
    block_dir = tmp_path / "runs" / block_id
    block_dir.mkdir(parents=True)

    tiers_summary = {
        "tiers": [
            {"tier": "easy", "cap": {"pass_pct": 100.0}},
            {"tier": "medium", "cap": {"pass_pct": 0.0}},
        ]
    }
    (block_dir / "tiers_summary.json").write_text(json.dumps(tiers_summary))

    reason = driver._all_failed(block_id)
    assert reason is None


def test_does_not_flag_missing_files(driver, tmp_path):
    """Test that _all_failed does not flag when result files are missing."""
    block_id = "test-block"
    # Don't create any result files

    reason = driver._all_failed(block_id)
    assert reason is None


def test_does_not_flag_unparseable_json(driver, tmp_path):
    """Test that _all_failed does not flag when JSON is unparseable."""
    block_id = "test-block"
    block_dir = tmp_path / "runs" / block_id
    block_dir.mkdir(parents=True)

    # Write invalid JSON
    (block_dir / "tiers_summary.json").write_text("{ invalid json }")

    reason = driver._all_failed(block_id)
    assert reason is None


def test_checks_pulled_results_fallback(driver, tmp_path):
    """Test that _all_failed checks pulled/run2/results as fallback location."""
    block_id = "test-block"
    pulled_dir = tmp_path / "pulled" / "run2" / "results" / block_id
    pulled_dir.mkdir(parents=True)

    eye_test = {
        "detail": {
            "checked": 5,
            "passed_count": 0,
        }
    }
    (pulled_dir / "eye_test.json").write_text(json.dumps(eye_test))

    reason = driver._all_failed(block_id)
    assert reason is not None
    assert "0/5" in reason


def test_flags_all_zero_phase_summary(driver, tmp_path):
    """Test that _all_failed flags phase/summary.json with all window counts at 0."""
    block_id = "test-block"
    block_dir = tmp_path / "runs" / block_id
    phase_dir = block_dir / "phase"
    phase_dir.mkdir(parents=True)

    summary = {
        "windows": {
            "prefill": 0,
            "decode": 0,
            "idle": 0,
        }
    }
    (phase_dir / "summary.json").write_text(json.dumps(summary))

    reason = driver._all_failed(block_id)
    assert reason is not None
    assert "phase" in reason


def test_does_not_flag_partial_windows(driver, tmp_path):
    """Test that _all_failed does not flag phase summary with some non-zero windows."""
    block_id = "test-block"
    block_dir = tmp_path / "runs" / block_id
    phase_dir = block_dir / "phase"
    phase_dir.mkdir(parents=True)

    summary = {
        "windows": {
            "prefill": 5,
            "decode": 0,
        }
    }
    (phase_dir / "summary.json").write_text(json.dumps(summary))

    reason = driver._all_failed(block_id)
    assert reason is None


def test_eye_test_with_zero_checked_does_not_flag(driver, tmp_path):
    """Test that _all_failed does not flag eye_test with 0 checked (vacuous pass)."""
    block_id = "test-block"
    block_dir = tmp_path / "runs" / block_id
    block_dir.mkdir(parents=True)

    eye_test = {
        "detail": {
            "checked": 0,
            "passed_count": 0,
        }
    }
    (block_dir / "eye_test.json").write_text(json.dumps(eye_test))

    reason = driver._all_failed(block_id)
    assert reason is None
