"""T17/T19: verifier core and `wattnot verify` on the 6 golden vectors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wattnot.verify import load_trusted, verify_receipt

S = Path(__file__).resolve().parents[1] / "verifier" / "samples"
EXPECTED = json.loads((S / "expected.json").read_text())


def test_six_vectors_three_real_three_forged():
    assert len(EXPECTED) == 6
    assert sum(k.startswith("real-") for k in EXPECTED) == 3
    assert sum(k.startswith("forged-") for k in EXPECTED) == 3


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_golden_vector_grade(name):
    r = verify_receipt(S / f"{name}.json", trusted=load_trusted())
    assert r.grade == EXPECTED[name]["grade"]
    steps = r.to_dict()["steps"]
    assert [s["name"] for s in steps] == ["schema", "sha256", "signature", "rekor", "grade"]
    assert steps[3]["ok"] is False and steps[3].get("log_index") is None  # offline only


def test_step_report_fields():
    d = verify_receipt(S / "real-measured.json").to_dict()
    sha = d["steps"][1]
    assert sha["expected"] == sha["got"] and len(sha["got"]) == 64
    assert d["steps"][2]["signer"].startswith("offline:")


def test_forged_edit_shows_both_hashes():
    sha = verify_receipt(S / "forged-edited.json").to_dict()["steps"][1]
    assert not sha["ok"] and sha["expected"] != sha["got"]


def test_untrusted_signer_is_rejected(tmp_path):
    t = tmp_path / "keys.json"
    t.write_text(json.dumps({"keys": [{"signer": "offline:0000000000000000"}]}))
    r = verify_receipt(S / "real-measured.json", trusted=load_trusted(t))
    assert r.grade == "UNVERIFIABLE"


def test_missing_bundle_and_bad_json(tmp_path):
    p = tmp_path / "r.json"
    p.write_text((S / "real-measured.json").read_text())
    assert verify_receipt(p).grade == "UNVERIFIABLE"
    p.write_text("{not json")
    assert verify_receipt(p).grade == "UNVERIFIABLE"


def test_not_our_spec(tmp_path):
    p = tmp_path / "r.json"
    d = json.loads((S / "real-measured.json").read_text())
    d["spec"] = "other/1"
    p.write_text(json.dumps(d))
    assert verify_receipt(p).grade == "UNVERIFIABLE"


def test_cli_verify_text_and_json(capsys):
    from wattnot.cli import main
    assert main(["verify", str(S / "real-measured.json")]) == 0
    out = capsys.readouterr().out
    for word in ("schema", "sha256", "signature", "rekor", "ESTIMATE"):
        assert word in out
    assert main(["verify", str(S / "forged-edited.json"), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["grade"] == "UNVERIFIABLE"


def test_cli_verify_all_samples(capsys):
    from wattnot.cli import main
    assert main(["verify", *[str(S / f"{n}.json") for n in sorted(EXPECTED)], "--json"]) == 1
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 6
