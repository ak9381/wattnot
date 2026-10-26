"""T15: safe headline (baseline vs FP8 + batching, 5 vs 5) on the fake backend."""

from __future__ import annotations

import json

import pytest

from wattnot.headline import FakeBackend, ModalBackend, safe_headline
from wattnot.receipt.sign import generate_test_key
from wattnot.verify import verify_receipt


def test_fake_headline_end_to_end(tmp_path):
    key = generate_test_key(tmp_path / "k.pem")
    out = safe_headline(FakeBackend(seed=1), root=tmp_path, key_path=key,
                        run_id="run_20261028_120000_fa0001", n_items=8)
    r = json.loads(out.receipt.read_text())
    # simulated numbers are never MEASURED
    assert r["measurement"]["label"] == "SIMULATED"
    assert r["subject"]["host"]["provider"] == "fake"
    assert r["candidate"]["params"]["quantization"] == "fp8"
    b, c = r["baseline"]["wh_per_req"], r["candidate"]["wh_per_req"]
    assert len(b["samples"]) == len(c["samples"]) == 5
    assert r["result"]["billable"] is True and r["result"]["billed_saving_pct"] > 0
    assert out.decision_passed
    rep = verify_receipt(out.receipt)
    assert rep.grade == "SIMULATED"  # fake backend never grades above SIMULATED
    types = [json.loads(x)["type"] for x in out.events.read_text().splitlines()]
    assert types[0] == "run.started" and types[-1] == "run.finished"
    assert types.count("gate.result") >= 3 and "action.applied" in types


def test_noisy_fake_is_not_billed(tmp_path):
    key = generate_test_key(tmp_path / "k.pem")
    out = safe_headline(FakeBackend(seed=2, saving=0.0, noise=0.02), root=tmp_path,
                        key_path=key, run_id="run_20261028_120000_fa0002", n_items=8)
    r = json.loads(out.receipt.read_text())
    assert r["grade"] == "NOISE" and r["result"]["billed_saving_pct"] == 0
    types = [json.loads(x)["type"] for x in out.events.read_text().splitlines()]
    assert "action.rolled_back" in types


def test_modal_backend_is_stubbed(tmp_path):
    with pytest.raises(NotImplementedError, match="T10"):
        ModalBackend().client(None)
