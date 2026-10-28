"""Joules per answer and per good answer: derivation, builder, verifier."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

from wattnot.cli import main
from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.joules import arm_joules, check_joules, joules_block
from wattnot.receipt.sign import sign
from wattnot.verify import load_trusted, verify_document, verify_receipt

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "receipts"
KEY = ROOT / "verifier" / "samples" / "test-key.pem"


def _arm(wh, pass_rate, rng=None):
    w = {"mean": wh, "sd": 0.0}
    if rng:
        w["range"] = {"low": rng[0], "high": rng[1]}
    return {"wh_per_req": w, "quality": {"fixed_pass_rate": pass_rate}}


def test_j_per_answer_is_wh_times_3600():
    a = arm_joules(_arm(0.05, 1.0))
    assert a["j_per_answer"] == 180.0 and a["j_per_good_answer"] == 180.0


def test_failing_answers_make_a_cheap_change_look_worse():
    cheap_but_bad = arm_joules(_arm(0.04, 0.5))
    pricey_but_good = arm_joules(_arm(0.05, 1.0))
    assert cheap_but_bad["j_per_answer"] < pricey_but_good["j_per_answer"]
    assert cheap_but_bad["j_per_good_answer"] > pricey_but_good["j_per_good_answer"]


def test_pass_rate_zero_or_missing_has_no_good_answer():
    assert arm_joules(_arm(0.05, 0.0))["j_per_good_answer"] is None
    arm = {"wh_per_req": {"mean": 0.05, "sd": 0}, "quality": {}}
    assert arm_joules(arm)["j_per_good_answer"] is None


def test_range_is_carried_through():
    a = arm_joules(_arm(0.05, 0.5, rng=(0.01, 0.09)))
    assert a["j_per_answer_range"] == {"low": 36.0, "high": 324.0}
    assert a["j_per_good_answer_range"] == {"low": 72.0, "high": 648.0}


def test_block_takes_its_label_from_the_measurement():
    r = json.loads((FIX / "estimate.json").read_text())
    b = joules_block(r)
    assert b["label"] == r["measurement"]["label"] and b["unit"] == "J"
    assert b["change_pct"]["j_per_answer"] == round(
        100 * (b["candidate"]["j_per_answer"] - b["baseline"]["j_per_answer"])
        / b["baseline"]["j_per_answer"], 4)


def test_builder_writes_the_block_and_it_checks_out():
    import importlib.util

    from wattnot.receipt import build_receipt
    spec = importlib.util.spec_from_file_location("mk", FIX / "make.py")
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    run = mk.run("run_20261028_100000_a00009", mk.BASE, mk.CAND)
    r = build_receipt(run, n_boot=200)
    assert "joules" in r and check_joules(r)[0]
    assert r["integrity"]["sha256"] == canonical_sha256(r)


def test_old_receipt_without_block_is_fine_and_gets_a_derived_view():
    p = ROOT / "verifier" / "samples" / "real-measured.json"
    rep = verify_receipt(p, trusted=load_trusted())
    assert rep.grade == "ESTIMATE"
    assert [s.name for s in rep.steps] == ["schema", "sha256", "signature", "rekor", "labels", "grade"]
    assert rep.joules and rep.joules["candidate"]["j_per_answer"] > 0


def test_good_block_adds_a_passing_step():
    rep = verify_receipt(ROOT / "verifier" / "vectors" / "real-joules.json", trusted=load_trusted())
    assert rep.grade == "ESTIMATE"
    assert [s.name for s in rep.steps][-3:] == ["joules", "labels", "grade"] and rep.steps[-3].ok


def test_edited_block_is_unverifiable_even_with_valid_hash_and_signature():
    rep = verify_receipt(ROOT / "verifier" / "vectors" / "forged-joules.json",
                         trusted=load_trusted())
    by = {s.name: s.ok for s in rep.steps}
    assert by["sha256"] and by["signature"] and not by["joules"]
    assert rep.grade == "UNVERIFIABLE"


def test_edit_that_ignores_the_hash_is_caught_by_the_hash(tmp_path):
    p = tmp_path / "r.json"
    shutil.copy(ROOT / "verifier" / "vectors" / "real-joules.json", p)
    shutil.copy(ROOT / "verifier" / "vectors" / "real-joules.sigstore.json",
                tmp_path / "r.sigstore.json")
    d = json.loads(p.read_text())
    d["joules"]["candidate"]["j_per_good_answer"] = 1.0
    p.write_text(json.dumps(d))
    assert verify_receipt(p).grade == "UNVERIFIABLE"


def test_receipt_missing_arm_numbers_does_not_crash_the_verifier():
    r = json.loads((ROOT / "verifier" / "vectors" / "real-joules.json").read_text())
    del r["candidate"]["wh_per_req"]
    ok, detail = check_joules(r)
    assert not ok and "cannot re-derive" in detail["error"]
    assert verify_document(r, None).grade == "UNVERIFIABLE"


def test_cli_joules_text_json_and_mismatch(tmp_path, capsys):
    good = ROOT / "verifier" / "vectors" / "real-joules.json"
    assert main(["receipt", "joules", str(good)]) == 0
    assert "J per good answer" in capsys.readouterr().out
    assert main(["receipt", "joules", str(good), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["matches_stored"] is True and out["joules"]["unit"] == "J"
    forged = ROOT / "verifier" / "vectors" / "forged-joules.json"
    assert main(["receipt", "joules", str(forged)]) == 1


def test_signing_a_receipt_with_a_block_keeps_the_hash(tmp_path):
    p = tmp_path / "r.json"
    shutil.copy(ROOT / "verifier" / "vectors" / "real-joules.json", p)
    before = copy.deepcopy(json.loads(p.read_text()))
    sign(p, no_rekor=True, key_path=KEY)
    after = json.loads(p.read_text())
    assert after["integrity"]["sha256"] == before["integrity"]["sha256"]
