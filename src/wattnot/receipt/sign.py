"""Sign a receipt and store the bundle next to it (`<id>.sigstore.json`).

Two paths:

* Offline (`--no-rekor`): an Ed25519 key on disk signs the receipt's sha256.
  Used in tests and dry runs. Nothing leaves the machine, and the grade can
  be at most ESTIMATE because there is no Rekor entry.
* Public: keyless Sigstore (GitHub OIDC) plus a Rekor upload. Rekor is
  public and permanent, so this path refuses to run unless
  WATTNOT_PUBLIC_SIGN_APPROVED=1 is set by the project owner for that run.

Signing only fills `integrity.signer` and `integrity.rekor`. Both sit in
the `integrity` block, which the hash leaves out, so the sha256 is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from wattnot.receipt.canon import canonical_sha256

OFFLINE_MEDIA_TYPE = "application/vnd.wattnot.offline-signature.v1+json"

# The local offline key used to sign run receipts automatically. $WATTNOT_SIGN_KEY
# points at a PEM file; otherwise the default below is tried. Nothing is ever created
# here and no key material is printed or returned.
KEY_ENV = "WATTNOT_SIGN_KEY"
DEFAULT_KEY = Path.home() / ".wattnot" / "test-key.pem"

# Written to integrity.signer when no local key is configured, so a reader can tell at a
# glance that the receipt was left unsigned on purpose (the run is never failed for this).
UNSIGNED = "unsigned: no local signing key configured"


class PublicSigningRefused(RuntimeError):
    """Raised when a public (Rekor) signature is asked for without approval."""


def generate_test_key(path: str | Path) -> Path:
    """Write a fresh Ed25519 test key (PEM). Never use it for public receipts."""

    path = Path(path)
    key = Ed25519PrivateKey.generate()
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    path.chmod(0o600)
    return path


def _raw_public(pub: Ed25519PublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def key_id(pub: Ed25519PublicKey) -> str:
    return hashlib.sha256(_raw_public(pub)).hexdigest()[:16]


def bundle_path(receipt_path: str | Path) -> Path:
    p = Path(receipt_path)
    return p.with_name(p.stem + ".sigstore.json")


def local_key_path(env: Mapping[str, str] | None = None) -> Path | None:
    """The offline key that run receipts are signed with, or None if none is configured.

    `$WATTNOT_SIGN_KEY` wins (an explicitly chosen path; if it does not exist, nothing is
    signed, rather than silently falling back). Otherwise the default `~/.wattnot/test-key.pem`
    is used when it exists. The path is never created here and the key is never read aloud.
    """

    env = os.environ if env is None else env
    raw = (env.get(KEY_ENV) or "").strip()
    p = Path(raw).expanduser() if raw else DEFAULT_KEY
    return p if p.is_file() else None


def sign_run_receipt(receipt_path: str | Path, *, key_path: str | Path | None = None,
                     env: Mapping[str, str] | None = None) -> tuple[Path | None, str]:
    """Sign a freshly written run receipt offline, automatically.

    Returns `(bundle_path, signer)`. If `key_path` is given it is used; otherwise the local
    key (`local_key_path`) is. When no key is available the receipt is clearly marked unsigned
    in its `integrity` block and `(None, UNSIGNED)` is returned -- a run is never failed just
    because no signing key is present. Marking the receipt unsigned does not change its hash
    (the `integrity` block is excluded from the canonical digest), and signing never upgrades a
    grade: it only fills `integrity.signer`/`rekor`.
    """

    receipt_path = Path(receipt_path)
    key = Path(key_path).expanduser() if key_path else local_key_path(env)
    if key is None or not key.is_file():
        receipt = json.loads(receipt_path.read_text())
        receipt["integrity"]["signer"] = UNSIGNED
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return None, UNSIGNED
    out = sign(receipt_path, no_rekor=True, key_path=key)
    signer = json.loads(receipt_path.read_text())["integrity"]["signer"]
    return out, signer


def sign(receipt_path: str | Path, *, no_rekor: bool = True,
         key_path: str | Path | None = None) -> Path:
    receipt_path = Path(receipt_path)
    receipt = json.loads(receipt_path.read_text())
    digest = canonical_sha256(receipt)
    if digest != receipt["integrity"]["sha256"]:
        raise ValueError("receipt hash does not match its content; rebuild it first")
    if not no_rekor:
        return _sign_public(receipt_path)
    if key_path is None:
        raise ValueError("offline signing needs --key PATH (a test key)")
    key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("offline key must be Ed25519")
    pub = key.public_key()
    kid = key_id(pub)
    bundle = {"mediaType": OFFLINE_MEDIA_TYPE, "sha256": digest,
              "signature": key.sign(bytes.fromhex(digest)).hex(),
              "public_key": _raw_public(pub).hex(), "key_id": kid, "rekor": None}
    out = bundle_path(receipt_path)
    out.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    receipt["integrity"]["signer"] = f"offline:{kid}"
    receipt["integrity"]["rekor"] = None
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return out


def verify_offline_bundle(receipt: Mapping, bundle: Mapping) -> tuple[bool, str]:
    if bundle.get("mediaType") != OFFLINE_MEDIA_TYPE:
        return False, "not an offline bundle"
    got = canonical_sha256(receipt)
    if got != bundle.get("sha256") or got != (receipt.get("integrity") or {}).get("sha256"):
        return False, "sha256 does not match the receipt"
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(bundle["public_key"]))
        pub.verify(bytes.fromhex(bundle["signature"]), bytes.fromhex(got))
    except (InvalidSignature, ValueError, KeyError):
        return False, "signature does not verify"
    return True, f"offline:{key_id(pub)}"


def _sign_public(receipt_path: Path) -> Path:  # pragma: no cover - needs owner approval
    if os.environ.get("WATTNOT_PUBLIC_SIGN_APPROVED") != "1":
        raise PublicSigningRefused(
            "public signing uploads to Rekor (public, permanent). Ask the project owner, "
            "then set WATTNOT_PUBLIC_SIGN_APPROVED=1 for that one run. Use --no-rekor to test.")
    try:
        from sigstore.models import ClientTrustConfig  # noqa: F401
        from sigstore.oidc import Issuer  # noqa: F401
        from sigstore.sign import SigningContext  # noqa: F401
    except ImportError as e:
        raise RuntimeError("install the 'sign' extra: pip install 'wattnot[sign]'") from e
    raise NotImplementedError("keyless signing is wired at the first approved public sign")
