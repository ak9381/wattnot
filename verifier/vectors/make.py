"""Rebuild the conformance vectors: python verifier/vectors/make.py

Nine vectors, each a receipt plus its offline signature bundle, and `expected.json` with the
grade a conforming verifier must print. Six are the original golden vectors (copied from
`verifier/samples`); three are new for spec v0.1:

  real-joules          a GPU receipt that carries a correct `joules` block
  real-cloud           a hosted-model receipt adapted from a cloud receipt (SIMULATED fixture)
  forged-joules        the `joules` block was edited, then the receipt was re-hashed and
                       re-signed with the (public) test key: hash and signature are fine,
                       but the block no longer matches the receipt's own numbers

TEST KEY ONLY: the key is derived from a public seed. Nothing here is a real result.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from wattnot.cloud import run as cloud_run
from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.cloud_adapter import from_cloud
from wattnot.receipt.grade import claimed_grade
from wattnot.receipt.joules import with_joules
from wattnot.receipt.sign import sign

HERE = Path(__file__).resolve().parent
SAMPLES = HERE.parent / "samples"
FIX = HERE.parents[1] / "tests" / "fixtures" / "receipts"
KEY = SAMPLES / "test-key.pem"
NOW = 1_792_000_000.0  # fixed clock so the cloud receipt id and time are reproducible

ORIGINAL = {k: v["grade"] for k, v in json.loads((SAMPLES / "expected.json").read_text()).items()}
NEW = {
    "real-joules": ("ESTIMATE", "carries a joules block that matches; signed offline"),
    "real-cloud": ("SIMULATED", "hosted-model receipt from the SIMULATED demo fixture; "
                                "never VALID, no significance test"),
    "forged-joules": ("UNVERIFIABLE", "joules block edited; hash and signature re-made, "
                                      "but the block does not match the arms"),
}


def _dump(p: Path, d: dict) -> None:
    p.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")


def _reseal(d: dict) -> dict:
    d["integrity"]["sha256"] = canonical_sha256(d)
    d["integrity"]["signer"] = None
    return d


def main() -> None:
    for f in SAMPLES.glob("*.json"):
        if f.name.startswith(("real-", "forged-")):
            shutil.copy(f, HERE / f.name)

    r = json.loads((FIX / "valid.json").read_text())
    r.pop("integrity")
    with_joules(r)
    r["grade"] = claimed_grade(r, rekor=False)
    r["integrity"] = {"canonicalization": "JCS RFC 8785", "sha256": canonical_sha256(r),
                      "prev_sha256": None, "signer": None, "rekor": None}
    p = HERE / "real-joules.json"
    _dump(p, r)
    sign(p, no_rekor=True, key_path=KEY)

    with tempfile.TemporaryDirectory() as td:
        transport, mode, _ = cloud_run.open_transport(live=False, fixture=cloud_run.DEMO_FIXTURE)
        path, _ = cloud_run.run(cloud_run.Options(), transport, mode, out_dir=td, now=NOW)
        cloud = json.loads(path.read_text())
    adapted = from_cloud(cloud)
    p = HERE / "real-cloud.json"
    _dump(p, adapted)
    sign(p, no_rekor=True, key_path=KEY)

    f = json.loads((HERE / "real-joules.json").read_text())
    f["joules"]["candidate"]["j_per_good_answer"] = round(
        f["joules"]["candidate"]["j_per_good_answer"] * 0.5, 6)
    _reseal(f)
    p = HERE / "forged-joules.json"
    _dump(p, f)
    sign(p, no_rekor=True, key_path=KEY)

    why = {k: v["why"] for k, v in json.loads((SAMPLES / "expected.json").read_text()).items()}
    expected = {k: {"grade": g, "why": why[k]} for k, g in ORIGINAL.items()}
    expected.update({k: {"grade": g, "why": w} for k, (g, w) in NEW.items()})
    _dump(HERE / "expected.json", expected)


if __name__ == "__main__":
    main()
