"""Provider profiles must match what the runners actually enforce (no network, no spend)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from wattnot import providers as P

ROOT = Path(__file__).resolve().parents[1]


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_baseten_caps_match_runner():
    br = _load("scripts/e2e/baseten_round.py", "_t_baseten_round")
    assert P.PROFILES["baseten"]["caps"]["round_hard_usd"] == br.HARD_CAP_USD == 20.0
    assert br.FAIR_ALLOWANCE["medium"] == 12000
    assert br.FAIR_ALLOWANCE["low"] == br.DEFAULT_ALLOWANCE["low"]
    assert set(br.ALL_SETS) >= set(P.PUBLIC_SETS)


def test_h100_rate_matches_driver_and_ctl():
    from wattnot.longrun import driver
    assert P.PROFILES["h100"]["caps"]["inr_per_h"] == driver.INR_PER_H
    assert P.PROFILES["h100"]["caps"]["public_booking_h"] == driver.PUBLIC_BOOKING_H
    assert driver.PUBLIC_BUDGET_CAP_INR >= driver.PUBLIC_BOOKING_H * driver.INR_PER_H


def test_modal_caps_match_stage():
    pytest.importorskip("modal")
    fs = _load("modal/full_stage.py", "_t_full_stage")
    c = P.PROFILES["modal"]["caps"]
    assert c["round_hard_usd"] == fs.ROUND_HARD_USD and c["smoke_hard_usd"] == fs.SMOKE_HARD_USD


def test_public_round_is_round_two_everywhere():
    for prov, rid in {"baseten": "r02", "modal": "r05", "h100": "r08", "combined": "r11"}.items():
        r = P.public_round(prov)
        assert r["round"] == rid and r["sets"] == ["gsm8k", "humaneval"] and r["items_per_arm"] == 100
    with pytest.raises(ValueError):
        P.public_round("aws")


def test_dry_commands_never_go_live():
    for prov in ("modal", "baseten", "h100", "combined"):
        for cmd in P.commands(prov, dry_run=True):
            joined = " ".join(cmd)
            assert "--live" not in joined and "modal_stage.py" not in joined and "baseten_stage.py" not in joined
            assert "gsm8k" in joined or "public" in joined


def test_public_plan_blocks(tmp_path):
    from wattnot.longrun import driver
    names = [b.id for b in driver.plan_glimmer_public(tmp_path)]
    assert any(n.startswith("m-public-") for n in names)
    assert "t3-copy" in names and "t0-setup" in names


def test_baseten_eye_uses_gauge_with_exact_check():
    from wattnot.vision.assets import load_eye_test
    from wattnot.vision.gate import _matches
    eye = load_eye_test(None, require_files=False)
    gauge = [it for it in eye.items if it.id == "gauge-reading"]
    assert gauge and gauge[0].kind == "number"
    want = gauge[0].answer
    assert _matches("number", want, f"The gauge reads {want}.")
    assert not _matches("number", want, str(float(want) + 2))
    assert not _matches("number", want, str(float(want) + 0.01))
