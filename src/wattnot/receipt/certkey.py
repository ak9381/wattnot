"""The real certificate signing key (Ed25519), kept on the box outside any repo.

Live rounds and certificate minting sign with it; the public sample key in
verifier/samples/ stays for tests and samples only. The path comes from
$WATTNOT_CERT_KEY, default ~/.wattnot/cert-signing-key.pem. A missing key fails
closed. The key is never printed, logged or copied; only its public half and
signer id leave this module.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

CERT_KEY_ENV = "WATTNOT_CERT_KEY"
DEFAULT_CERT_KEY = Path.home() / ".wattnot" / "cert-signing-key.pem"
SIGNER_KIND = "ed25519-wattnot"
TEST_KIND = "ed25519-test"
TEST_LABEL = "test-signed (public sample key)"
TRUSTED = Path(__file__).resolve().parents[3] / "verifier" / "trusted-keys.json"


class CertKeyMissing(RuntimeError):
    """No real signing key: live receipts and certificates are refused (fail closed)."""


def cert_key_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    raw = (env.get(CERT_KEY_ENV) or "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_CERT_KEY


def require_cert_key(env: Mapping[str, str] | None = None) -> Path:
    p = cert_key_path(env)
    if not p.is_file():
        raise CertKeyMissing(
            f"certificate signing key not found at {p} (set ${CERT_KEY_ENV}); refusing to sign a "
            "live receipt or certificate with the public test key")
    return p


def signer_id(path: str | Path) -> str:
    from cryptography.hazmat.primitives import serialization

    from wattnot.receipt.sign import key_id
    key = serialization.load_pem_private_key(Path(path).read_bytes(), None)
    return "offline:" + key_id(key.public_key())


def trusted_kinds(path: str | Path | None = None) -> dict[str, str]:
    p = Path(path) if path else TRUSTED
    return {k["signer"]: k.get("kind", "") for k in json.loads(p.read_text())["keys"]}
