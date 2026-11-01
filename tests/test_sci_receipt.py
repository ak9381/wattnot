"""Energy + carbon + water as one signed SCI block (GSF SCI): builder, verifier, CLI, EU, proof."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from wattnot.cli import main
from wattnot.receipt import build_receipt
from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.sci import GRID, check_sci, grid_from_env, sci_block
from wattnot.receipt.sign import sign
from wattnot.report.eu import BANNED, proof_fields
from wattnot.verify import verify_receipt

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "receipts"
KEY = ROOT / "verifier" / "samples" / "test-key.pem"


def _built():
    spec = importlib.util.spec_from_file_location("mk", FIX / "make.py")
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    return build_receipt(mk.run("run_20261028_100000_a00009", mk.BASE, mk.CAND), n_boot=200)


def _signed(tmp_path, r):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(r))
    sign(p, no_rekor=True, key_path=KEY)
    return p


def test_builder_emits_sci_and_e_times_i_holds():
    r = _built()
    s = r["sci"]
    assert check_sci(r)[0] and r["integrity"]["sha256"] == canonical_sha256(r)
    c = s["arms"]["candidate"]
    assert s["I"]["gco2e_per_kwh"] == 727.0 and "CEA" in s["I"]["source"]
    assert s["I"]["label"] == "ESTIMATE" and s["M"]["status"] == "NOT_AVAILABLE"
    assert c["gco2e_per_request"] == pytest.approx(c["E_kwh_per_request"] * 727, rel=1e-8)
    assert c["water_ml_per_request"] == pytest.approx(c["E_kwh_per_request"] * 1000 * 0.36,
                                                      rel=1e-8)
    text = json.dumps(s).lower()
    assert not any(b in text for b in BANNED)


def test_verify_shows_sci_and_passes(tmp_path, capsys):
    p = _signed(tmp_path, _built())
    rep = verify_receipt(p)
    assert {s.name: s.ok for s in rep.steps}["sci"]
    main(["verify", str(p)])
    out = capsys.readouterr().out
    assert "gCO2e per correct answer" in out and "727" in out and "CEA" in out


def test_tampered_sci_fails_even_when_rehashed_and_resigned(tmp_path):
    r = _built()
    r["sci"]["arms"]["candidate"]["gco2e_per_correct"] *= 0.5   # halve the carbon
    r["integrity"]["sha256"] = None
    r["integrity"]["sha256"] = canonical_sha256({k: v for k, v in r.items()})
    assert not check_sci(r)[0]
    p = _signed(tmp_path, r)
    rep = verify_receipt(p)
    assert not {s.name: s.ok for s in rep.steps}["sci"] and rep.grade == "UNVERIFIABLE"


def test_tampered_grid_factor_fails():
    r = _built()
    r["sci"]["I"]["gco2e_per_kwh"] = 1.0
    assert not check_sci(r)[0]
    r = _built()
    r["sci"]["water"]["wue"]["l_per_kwh"] = 0.01
    assert not check_sci(r)[0]


def test_standalone_verifier_checks_sci():
    spec = importlib.util.spec_from_file_location("sv", ROOT / "verifier" / "wattnot_verify.py")
    sv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sv)
    r = _built()
    assert sv.check_sci(r)[0]
    rep = sv.verify_document(r)
    assert any(s["name"] == "sci" and s["ok"] for s in rep["steps"])
    r["sci"]["arms"]["candidate"]["water_ml_per_correct"] = 0.0
    assert not sv.check_sci(r)[0]


def test_grid_is_configurable_and_must_be_cited():
    assert grid_from_env({})["gco2e_per_kwh"] == GRID["IN"]["gco2e_per_kwh"]
    g = grid_from_env({"WATTNOT_GRID_GCO2E_PER_KWH": "400", "WATTNOT_GRID_SOURCE": "X 2024"})
    assert g["gco2e_per_kwh"] == 400.0 and g["label"] == "ESTIMATE"
    with pytest.raises(ValueError):
        grid_from_env({"WATTNOT_GRID_GCO2E_PER_KWH": "400"})


def test_proof_receipt_gets_sci_and_eu_shows_it():
    r = json.loads((ROOT / "tests" / "fixtures" / "proof-receipt-rehearsal.json").read_text())
    r["sci"] = sci_block(r)
    assert check_sci(r)[0]
    r["integrity"] = {"sha256": None, "signer": None, "rekor": None}
    r["integrity"]["sha256"] = canonical_sha256(r)
    rows = {f["field"]: f for f in proof_fields(r)}
    assert rows["sci_per_request"]["label"] == "ESTIMATE"
    assert rows["grid_intensity"]["value"] == 727.0
    assert rows["sci_embodied_m"]["label"] == "NOT_AVAILABLE"


def test_orchestrate_attaches_sci_before_hashing():
    src = (ROOT / "src" / "wattnot" / "proof" / "orchestrate.py").read_text()
    i, j = src.index('body["sci"] = sci_block(body)'), src.index("digest = canonical_sha256(body)")
    assert i < j
