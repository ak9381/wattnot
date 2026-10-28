"""Tests for SAM hotspot sets the cap (src/wattnot/sam/hotspot_cap.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wattnot.receipt.build import build_receipt, verify_hash
from wattnot.sam.hotspot_cap import (
    DEFAULT_MIN_CAP_W,
    DEFAULT_STEP_W,
    DEFAULT_STOCK_CAP_W,
    compute_mask_sha256,
    detect_hotspot_mask,
    plan_hotspot_cap,
)

FIXTURE_RUN = {
    "run_id": "run_test_hotspot",
    "subject": {"model": "llama-8b", "host": "test-box", "mode": "mock", "workload": {"n_requests": 10}},
    "baseline": {
        "params": {"cap_w": 400},
        "reps": 2,
        "wh_per_req": {"mean": 2.5, "samples": [2.5, 2.4]},
        "p99_ms": 120.0,
        "quality": {"fixed_pass_rate": 0.95},
    },
    "candidate": {
        "params": {"cap_w": 350},
        "reps": 2,
        "wh_per_req": {"mean": 2.0, "samples": [2.0, 1.9]},
        "p99_ms": 130.0,
        "quality": {"fixed_pass_rate": 0.95},
    },
    "measurement": {"meter": "nvml", "hz": 10, "csv": "trace.csv", "label": "SIMULATED"},
}


def test_hotspot_cap_derates_only_target_gpu() -> None:
    initial_caps = {0: 400, 1: 400, 2: 400, 3: 400}
    plan = plan_hotspot_cap(gpu_caps=initial_caps, target_gpu=1, step_w=50, min_cap_w=250, mock=True)

    decision = plan["decision"]
    assert decision["gpu_id"] == 1
    assert decision["old_cap_w"] == 400
    assert decision["new_cap_w"] == 350
    assert decision["delta_w"] == -50
    assert decision["min_cap_w"] == 250
    assert decision["label"] == "ADVICE"

    # Verify only target GPU was changed, others remain unchanged
    after = plan["gpu_caps_after"]
    assert after[1] == 350
    assert after[0] == 400
    assert after[2] == 400
    assert after[3] == 400

    # Verify mask and sha256
    mask = plan["mask"]
    assert mask["label"] == "MOCK"
    assert len(mask["bbox"]) == 4
    assert len(mask["polygon"]) >= 4
    assert mask["sha256"] == compute_mask_sha256(mask)
    assert len(plan["reason"]) < 200


def test_hotspot_cap_enforces_lower_bound() -> None:
    # Starting at 270W with step 50W and min 250W -> should be bounded to 250W (not 220W)
    plan = plan_hotspot_cap(gpu_caps={1: 270}, target_gpu=1, step_w=50, min_cap_w=250, mock=True)
    assert plan["decision"]["new_cap_w"] == 250
    assert plan["decision"]["delta_w"] == -20

    # Already at min_cap_w -> held at min_cap_w
    plan_at_min = plan_hotspot_cap(gpu_caps={1: 250}, target_gpu=1, step_w=50, min_cap_w=250, mock=True)
    assert plan_at_min["decision"]["new_cap_w"] == 250
    assert plan_at_min["decision"]["delta_w"] == 0

    # Already below min_cap_w -> a derate must never RAISE the cap
    plan_below = plan_hotspot_cap(gpu_caps={1: 240}, target_gpu=1, step_w=50, min_cap_w=250, mock=True)
    assert plan_below["decision"]["new_cap_w"] == 240
    assert plan_below["decision"]["delta_w"] == 0
    assert plan_below["gpu_caps_after"][1] == 240
    assert "held at 240W" in plan_below["reason"]


def test_hotspot_receipt_fills_missing_mask_sha256() -> None:
    # build_receipt must compute the mask hash itself when the plan omits it
    plan = plan_hotspot_cap(target_gpu=1, mock=True)
    plan = json.loads(json.dumps(plan))
    expected_sha = plan["mask"].pop("sha256")
    receipt = build_receipt(FIXTURE_RUN, hotspot_cap=plan)
    assert receipt["measurement"]["hotspot_cap"]["mask"]["sha256"] == expected_sha


def test_hotspot_cap_in_receipt_is_signed_and_verified() -> None:
    plan = plan_hotspot_cap(target_gpu=1, step_w=50, min_cap_w=250, mock=True)
    receipt = build_receipt(FIXTURE_RUN, hotspot_cap=plan)

    # Verify hotspot_cap exists in measurement and at root
    assert "hotspot_cap" in receipt["measurement"]
    hc = receipt["measurement"]["hotspot_cap"]
    assert hc["decision"]["gpu_id"] == 1
    assert hc["decision"]["old_cap_w"] == 400
    assert hc["decision"]["new_cap_w"] == 350
    assert hc["mask"]["sha256"] == plan["mask"]["sha256"]
    assert hc["reason"] == plan["reason"]

    # Verify integrity hash covers the hotspot cap
    ok, want, got = verify_hash(receipt)
    assert ok is True
    assert want == got

    # Tampering with hotspot cap invalidates the signature hash
    receipt_copy = json.loads(json.dumps(receipt))
    receipt_copy["measurement"]["hotspot_cap"]["decision"]["new_cap_w"] = 300
    ok_tampered, _, _ = verify_hash(receipt_copy)
    assert ok_tampered is False


def test_hotspot_cap_from_events() -> None:
    plan = plan_hotspot_cap(target_gpu=2, step_w=50, min_cap_w=250, mock=True)
    events = [{"type": "sam.hotspot_cap", "data": plan}]
    receipt = build_receipt(FIXTURE_RUN, events=events)
    assert receipt["measurement"]["hotspot_cap"]["decision"]["gpu_id"] == 2
    assert receipt["measurement"]["hotspot_cap"]["decision"]["new_cap_w"] == 350
