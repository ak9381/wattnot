import json

import pytest

from wattnot import launch_check as L
from wattnot.receipt import certkey
from wattnot.receipt.sign import generate_test_key


def test_missing_key_fails_closed(tmp_path):
    with pytest.raises(certkey.CertKeyMissing):
        certkey.require_cert_key({"WATTNOT_CERT_KEY": str(tmp_path / "nope.pem")})


def test_env_path_wins(tmp_path):
    k = generate_test_key(tmp_path / "k.pem")
    assert certkey.require_cert_key({"WATTNOT_CERT_KEY": str(k)}) == k
    assert certkey.signer_id(k).startswith("offline:")


def test_preflight_rejects_untrusted_key(tmp_path):
    k = generate_test_key(tmp_path / "k.pem")
    r = L.check_cert_key({"WATTNOT_CERT_KEY": str(k)})
    assert not r["ok"] and "kind=None" in r["detail"]
    assert not L.check_cert_key({"WATTNOT_CERT_KEY": str(tmp_path / "x.pem")})["ok"]


def test_trusted_keys_has_real_and_test_kinds():
    kinds = certkey.trusted_kinds()
    assert "ed25519-wattnot" in kinds.values() and "ed25519-test" in kinds.values()
    raw = (certkey.TRUSTED).read_text()
    assert "PRIVATE" not in raw and "WNC-0001" in raw


def test_test_signed_label(tmp_path):
    from wattnot.verify import load_trusted
    t = load_trusted()
    assert t["offline:56475aa75463474c"] == "ed25519-test"
    import wattnot.verify as V
    assert V.TRUST_LABELS["ed25519-test"] == "test-signed (public sample key)"


def test_flow_requires_cert_key_when_live(tmp_path, monkeypatch):
    from wattnot import flow
    monkeypatch.setenv("WATTNOT_REQUIRE_CERT_KEY", "1")
    monkeypatch.setenv("WATTNOT_CERT_KEY", str(tmp_path / "missing.pem"))
    with pytest.raises(certkey.CertKeyMissing):
        flow._offline_key(tmp_path)
    k = generate_test_key(tmp_path / "real.pem")
    monkeypatch.setenv("WATTNOT_CERT_KEY", str(k))
    assert flow._offline_key(tmp_path) == k
