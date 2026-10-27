"""The GPU-host and demo appliers: a fake nvidia-smi run, nothing real."""
from __future__ import annotations

import subprocess

import pytest

from wattnot.approvals import model as m
from wattnot.approvals.appliers import (
    ApplyError,
    MockPowerCapApplier,
    NvidiaSmiApplier,
    demo_appliers,
    standard,
)
from wattnot.approvals.store import Store


def cap_proposal(after, gpu=0):
    return {"id": "p1", "kind": m.POWER_CAP,
            "change": {"gpu": gpu, "before": {"power_cap_w": 700},
                       "after": {"power_cap_w": after}}}


class FakeSmi:
    """A fake `subprocess.run` for nvidia-smi: keeps one limit per GPU."""

    def __init__(self, caps=None, stuck=False) -> None:
        self.caps = dict(caps or {0: 700})
        self.stuck = stuck
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        gpu = int(argv[argv.index("-i") + 1])
        if "--query-gpu=power.limit" in argv:
            value = self.caps.get(gpu, 700) if not self.stuck else 700
            return subprocess.CompletedProcess(argv, 0, stdout=f"{value}\n", stderr="")
        watts = int(argv[argv.index("-pl") + 1])
        if not self.stuck:
            self.caps[gpu] = watts
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


def test_smi_apply_and_rollback():
    fake = FakeSmi()
    ap = NvidiaSmiApplier(run=fake)
    token = ap.apply(cap_proposal(500))
    assert token == {"gpu": 0, "before_w": 700, "after_w": 500}
    assert fake.caps[0] == 500
    assert any("-pl" in c for c in fake.calls)
    ap.rollback(token, cap_proposal(500))
    assert fake.caps[0] == 700


def test_smi_readback_mismatch_restores_and_fails():
    fake = FakeSmi(stuck=True)  # the card ignores the write
    ap = NvidiaSmiApplier(run=fake)
    with pytest.raises(ApplyError):
        ap.apply(cap_proposal(500))


def test_smi_missing_binary_is_an_apply_error():
    def norun(argv, **kw):
        raise FileNotFoundError("nvidia-smi")
    with pytest.raises(ApplyError):
        NvidiaSmiApplier(run=norun).apply(cap_proposal(500))


def test_mock_applier_never_touches_hardware():
    ap = MockPowerCapApplier(stock_w=700)
    token = ap.apply(cap_proposal(500))
    assert token == {"gpu": 0, "before_w": 700, "after_w": 500}
    ap.rollback(token, cap_proposal(500))
    assert ap.apply(cap_proposal(600))["before_w"] == 700


def test_demo_appliers_merge_with_standard(tmp_path):
    store = Store(tmp_path / "p")
    merged = {**standard(store), **demo_appliers(stock_w=700)}
    assert set(merged) >= {m.POWER_CAP, m.VLLM_CONFIG, m.FLEX_SHED}
    assert isinstance(merged[m.POWER_CAP], MockPowerCapApplier)
    token = merged[m.POWER_CAP].apply(cap_proposal(500))
    assert token["after_w"] == 500
