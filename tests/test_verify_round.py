"""`wattnot verify` on round receipts (the e2e receipt.json in replays/)."""
import json
import shutil
from pathlib import Path

import pytest

from wattnot.verify import load_trusted, verify_receipt

ROOT = Path(__file__).resolve().parents[1]
REPLAYS = sorted((ROOT / "replays").glob("*/receipt.json"))


@pytest.mark.parametrize("path", REPLAYS, ids=lambda p: p.parent.name)
def test_replay_round_receipts_pass(path):
    r = verify_receipt(path, trusted=load_trusted())
    steps = {s.name: s for s in r.steps}
    assert steps["schema"].ok and steps["sha256"].ok and steps["signature"].ok
    assert steps["signature"].detail.get("trusted") is False  # per-run key, flagged
    assert r.grade == "ESTIMATE"


def _copy(tmp_path):
    src = REPLAYS[0]
    shutil.copy(src, tmp_path / "receipt.json")
    shutil.copy(src.with_name("receipt.sigstore.json"), tmp_path / "receipt.sigstore.json")
    return tmp_path / "receipt.json"


def test_edited_round_receipt_fails(tmp_path):
    p = _copy(tmp_path)
    doc = json.loads(p.read_text())
    doc["results"]["accuracy_with"] = 1.0
    p.write_text(json.dumps(doc))
    r = verify_receipt(p, trusted=load_trusted())
    assert r.grade == "UNVERIFIABLE"


def test_bad_signature_fails(tmp_path):
    p = _copy(tmp_path)
    b = p.with_name("receipt.sigstore.json")
    bd = json.loads(b.read_text())
    bd["signature"] = "00" * 64
    b.write_text(json.dumps(bd))
    r = verify_receipt(p, trusted=load_trusted())
    assert not next(s for s in r.steps if s.name == "signature").ok
    assert r.grade == "UNVERIFIABLE"


def test_missing_bundle_fails(tmp_path):
    p = _copy(tmp_path)
    p.with_name("receipt.sigstore.json").unlink()
    assert verify_receipt(p, trusted=load_trusted()).grade == "UNVERIFIABLE"


def test_round_without_results_is_schema_fail(tmp_path):
    p = _copy(tmp_path)
    doc = json.loads(p.read_text())
    del doc["results"]
    p.write_text(json.dumps(doc))
    r = verify_receipt(p, trusted=load_trusted())
    assert not r.steps[0].ok and r.grade == "UNVERIFIABLE"


ATTACH = sorted((ROOT / "replays").glob("*/blocks/*/attachment.json"))


@pytest.mark.parametrize("path", ATTACH, ids=lambda p: p.parent.name)
def test_replay_attachments_pass(path):
    r = verify_receipt(path, trusted=load_trusted())
    assert r.steps[0].ok and r.grade == "ESTIMATE"


def test_edited_attachment_fails(tmp_path):
    src = ATTACH[0]
    shutil.copy(src, tmp_path / "attachment.json")
    shutil.copy(src.with_name("attachment.sigstore.json"), tmp_path / "attachment.sigstore.json")
    p = tmp_path / "attachment.json"
    doc = json.loads(p.read_text())
    doc["block"] = "edited"
    p.write_text(json.dumps(doc))
    assert verify_receipt(p, trusted=load_trusted()).grade == "UNVERIFIABLE"
