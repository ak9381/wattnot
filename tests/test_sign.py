"""T14: signing. Offline Ed25519 test key only; no Rekor, nothing public."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from wattnot.receipt.sign import (
    PublicSigningRefused,
    generate_test_key,
    sign,
    verify_offline_bundle,
)

RC = Path(__file__).parent / "fixtures" / "receipts"


@pytest.fixture
def receipt(tmp_path):
    p = tmp_path / "valid.json"
    shutil.copy(RC / "valid.json", p)
    return p


def test_offline_sign_round_trip(tmp_path, receipt):
    key = generate_test_key(tmp_path / "test-key.pem")
    bundle = sign(receipt, no_rekor=True, key_path=key)
    assert bundle.name == "valid.sigstore.json"
    r = json.loads(receipt.read_text())
    b = json.loads(bundle.read_text())
    assert b["rekor"] is None and b["sha256"] == r["integrity"]["sha256"]
    assert r["integrity"]["signer"].startswith("offline:")
    ok, why = verify_offline_bundle(r, b)
    assert ok, why


def test_offline_signature_fails_after_an_edit(tmp_path, receipt):
    key = generate_test_key(tmp_path / "k.pem")
    b = json.loads(sign(receipt, no_rekor=True, key_path=key).read_text())
    r = json.loads(receipt.read_text())
    r["result"]["billed_saving_pct"] = 50.0
    ok, why = verify_offline_bundle(r, b)
    assert not ok and "sha256" in why


def test_tampered_signature_is_rejected(tmp_path, receipt):
    key = generate_test_key(tmp_path / "k.pem")
    b = json.loads(sign(receipt, no_rekor=True, key_path=key).read_text())
    sig = bytearray(bytes.fromhex(b["signature"]))
    sig[0] ^= 1
    b["signature"] = sig.hex()
    assert not verify_offline_bundle(json.loads(receipt.read_text()), b)[0]


def test_signing_does_not_change_the_hash(tmp_path, receipt):
    from wattnot.receipt import verify_hash
    sign(receipt, no_rekor=True, key_path=generate_test_key(tmp_path / "k.pem"))
    assert verify_hash(json.loads(receipt.read_text()))[0]


def test_public_signing_is_refused_without_explicit_approval(receipt, monkeypatch):
    monkeypatch.delenv("WATTNOT_PUBLIC_SIGN_APPROVED", raising=False)
    with pytest.raises(PublicSigningRefused):
        sign(receipt, no_rekor=False)


def test_cli_sign_offline(tmp_path, receipt, capsys):
    from wattnot.cli import main
    key = generate_test_key(tmp_path / "k.pem")
    assert main(["receipt", "sign", str(receipt), "--no-rekor", "--key", str(key)]) == 0
    assert "offline" in capsys.readouterr().out
