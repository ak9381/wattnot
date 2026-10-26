"""Rebuild the 6 golden vectors: 3 real, 3 forged.

    python verifier/samples/make.py

TEST KEY ONLY: test-key.pem is derived from a fixed public seed so the
vectors are reproducible. It proves nothing; never sign real receipts with it.
All signatures here are offline (no Rekor), so the best real grade is ESTIMATE.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.sign import sign

HERE = Path(__file__).resolve().parent
FIX = HERE.parents[1] / "tests" / "fixtures" / "receipts"
SEED = bytes(range(32))           # public, fixed: test vectors only
OTHER_SEED = bytes(range(1, 33))  # a second, "attacker" test key

EXPECTED = {
    "real-measured":   ("ESTIMATE", "MEASURED, signed offline: no Rekor so not VALID"),
    "real-estimate":   ("ESTIMATE", "ESTIMATE label"),
    "real-noise":      ("NOISE", "Welch p >= 0.05: saving not billed"),
    "forged-edited":   ("UNVERIFIABLE", "billed saving edited after signing"),
    "forged-rehashed": ("UNVERIFIABLE", "edited and re-hashed, old signature kept"),
    "forged-otherkey": ("UNVERIFIABLE", "edited, re-hashed, re-signed by another key "
                                        "but still names the original signer"),
}


def _key(path: Path, seed: bytes) -> Path:
    k = Ed25519PrivateKey.from_private_bytes(seed)
    path.write_bytes(k.private_bytes(serialization.Encoding.PEM,
                                     serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    return path


def _load(p: Path) -> dict:
    return json.loads(p.read_text())


def _dump(p: Path, d: dict) -> None:
    p.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")


def _real(name: str, src: str, key: Path) -> Path:
    p = HERE / f"{name}.json"
    shutil.copy(FIX / src, p)
    sign(p, no_rekor=True, key_path=key)
    return p


def main() -> None:
    key = _key(HERE / "test-key.pem", SEED)
    real = _real("real-measured", "valid.json", key)
    _real("real-estimate", "estimate.json", key)
    _real("real-noise", "noise.json", key)
    good = _load(real.with_name("real-measured.sigstore.json"))

    # 1. edit a number, keep hash and signature
    r = _load(real)
    r["result"]["billed_saving_pct"] = round(r["result"]["billed_saving_pct"] * 3 + 1, 2)
    _dump(HERE / "forged-edited.json", r)
    _dump(HERE / "forged-edited.sigstore.json", good)

    # 2. edit, recompute the hash, keep the old signature
    r["integrity"]["sha256"] = canonical_sha256(r)
    _dump(HERE / "forged-rehashed.json", r)
    _dump(HERE / "forged-rehashed.sigstore.json", {**good, "sha256": r["integrity"]["sha256"]})

    # 3. edit, re-hash, re-sign with another key, but keep the trusted signer name
    p = HERE / "forged-otherkey.json"
    _dump(p, r)
    other = _key(HERE / ".other-key.tmp.pem", OTHER_SEED)
    sign(p, no_rekor=True, key_path=other)
    other.unlink()
    r2 = _load(p)
    r2["integrity"]["signer"] = f"offline:{good['key_id']}"
    _dump(p, r2)

    _dump(HERE / "expected.json", {k: {"grade": g, "why": w} for k, (g, w) in EXPECTED.items()})


if __name__ == "__main__":
    main()
