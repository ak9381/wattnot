"""Run receipts (Baseten and Modal) are signed automatically, offline, with the local key.

No network, no paid calls: every receipt here comes from the bundled fixture replayed through
a temp Ed25519 key. Signing fills only the integrity block, so it never changes the hash and
never upgrades an honest grade (Baseten/Modal energy stays an ESTIMATE).
"""

from __future__ import annotations

import json
from pathlib import Path

from wattnot.cloud import receipt as rc
from wattnot.cloud import run as rn
from wattnot.cloud.transport import FixtureTransport, Recorder
from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.cloud_adapter import from_cloud
from wattnot.receipt.grade import claimed_grade
from wattnot.receipt.sign import (
    UNSIGNED,
    bundle_path,
    generate_test_key,
    local_key_path,
    sign_run_receipt,
    verify_offline_bundle,
)

NOW = 1_790_000_000.0


def _baseten(tmp_path, key, out="o"):
    """A signed Baseten cloud run receipt (synthetic replay -> SIMULATED)."""
    ft = FixtureTransport.load(rn.DEMO_FIXTURE)
    return rn.run(rn.Options(), ft, "replay-synthetic", out_dir=tmp_path / out, now=NOW,
                  sign_key=str(key))


def _measured_modal(tmp_path, key, out="m"):
    """A MEASURED cloud receipt relabelled as a Modal-hosted run, then re-sealed and signed.

    `mode="live"` over the recorded fixture makes no network call but yields MEASURED labels;
    the provider is rewritten to `modal` so from_cloud produces a Modal-hosted spec receipt.
    """
    rec = Recorder(FixtureTransport.load(rn.DEMO_FIXTURE))
    path, _ = rn.run(rn.Options(), rec, "live", out_dir=tmp_path / out, now=NOW)
    r = json.loads(path.read_text())
    r["provider"] = "modal"
    r["endpoint"] = "https://example.modal.run/v1"
    rc.reseal(r)  # recompute integrity.sha256 for the new content
    path.write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    bundle, signer = sign_run_receipt(path, key_path=str(key))
    return path, json.loads(path.read_text()), bundle, signer


def test_baseten_run_receipt_is_signed_automatically(tmp_path):
    key = generate_test_key(tmp_path / "k.pem")
    path, r = _baseten(tmp_path, key)
    assert r["provider"] == "baseten"
    assert r["integrity"]["signer"].startswith("offline:")
    # signing only filled the integrity block -> the canonical hash is untouched
    assert rc.verify_hash(r)[0] and r["integrity"]["sha256"] == canonical_sha256(r)
    b = json.loads(bundle_path(path).read_text())
    ok, signer = verify_offline_bundle(r, b)
    assert ok and signer == r["integrity"]["signer"]
    assert b["rekor"] is None  # offline only: nothing public


def test_modal_run_receipt_is_signed_automatically(tmp_path):
    key = generate_test_key(tmp_path / "k.pem")
    path, r, bundle, signer = _measured_modal(tmp_path, key)
    assert r["provider"] == "modal" and signer.startswith("offline:")
    ok, why = verify_offline_bundle(r, json.loads(Path(bundle).read_text()))
    assert ok, why
    # the adapted spec receipt keeps the Modal provider and an honest ESTIMATE energy
    ad = from_cloud(r)
    assert ad["subject"]["host"]["provider"] == "modal"
    assert ad["grade"] == "ESTIMATE" and ad["joules"]["label"] == "ESTIMATE"


def test_a_tampered_receipt_fails_verification(tmp_path):
    key = generate_test_key(tmp_path / "k.pem")
    path, r = _baseten(tmp_path, key)
    b = json.loads(bundle_path(path).read_text())
    assert verify_offline_bundle(r, b)[0]
    # change a headline number: the signed sha256 no longer matches the content
    r["totals"]["saving_usd"] += 1.0
    ok, why = verify_offline_bundle(r, b)
    assert not ok and "sha256" in why
    # flip one byte of the signature over the untouched receipt: rejected as well
    tampered = dict(b)
    sig = bytearray(bytes.fromhex(b["signature"]))
    sig[0] ^= 1
    tampered["signature"] = sig.hex()
    assert not verify_offline_bundle(json.loads(path.read_text()), tampered)[0]


def test_signing_does_not_change_the_grade(tmp_path):
    key = generate_test_key(tmp_path / "k.pem")
    # a MEASURED Baseten receipt before any signature is applied
    rec = Recorder(FixtureTransport.load(rn.DEMO_FIXTURE))
    path, _ = rn.run(rn.Options(), rec, "live", out_dir=tmp_path / "g", now=NOW)
    before = json.loads(path.read_text())
    assert before["label"] == "MEASURED" and before["labels"]["energy"] == "ESTIMATE"
    graded_before = from_cloud(before)
    assert graded_before["grade"] == "ESTIMATE"  # hosted: capped at ESTIMATE

    bundle, signer = sign_run_receipt(path, key_path=str(key))
    after = json.loads(path.read_text())
    assert signer.startswith("offline:") and bundle is not None
    # content labels are unchanged, and the grade is identical after signing
    assert after["labels"] == before["labels"]
    assert from_cloud(after)["grade"] == graded_before["grade"] == "ESTIMATE"
    # even if a Rekor entry existed, a hosted receipt can never be lifted above ESTIMATE
    assert claimed_grade(from_cloud(after), rekor=True) == "ESTIMATE"


def test_no_local_key_marks_unsigned_and_never_fails(tmp_path):
    ft = FixtureTransport.load(rn.DEMO_FIXTURE)
    path, _ = rn.run(rn.Options(), ft, "replay-synthetic", out_dir=tmp_path / "o", now=NOW,
                     sign_key=str(generate_test_key(tmp_path / "k.pem")))
    # ask to sign with a configured-but-missing key: no exception, receipt marked unsigned
    bundle, signer = sign_run_receipt(path, env={"WATTNOT_SIGN_KEY": str(tmp_path / "nope.pem")})
    assert bundle is None and signer == UNSIGNED
    r = json.loads(path.read_text())
    assert r["integrity"]["signer"] == UNSIGNED
    assert rc.verify_hash(r)[0]  # the unsigned label did not change the hash


def test_local_key_path_resolves_env_over_default(tmp_path):
    key = generate_test_key(tmp_path / "k.pem")
    assert local_key_path({"WATTNOT_SIGN_KEY": str(key)}) == key
    # set but absent -> None (no silent fallback to the default path)
    assert local_key_path({"WATTNOT_SIGN_KEY": str(tmp_path / "missing.pem")}) is None
