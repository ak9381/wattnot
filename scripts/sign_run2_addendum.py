"""Wrap a LIVE section as a signed addendum linked to the RUN2 receipt (parent hash unchanged).

usage: sign_run2_addendum.py <section.json> <name>
Signs with the public test key (verifier/samples/test-key.pem), same as the RUN2 receipt.
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.sign import sign

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "receipts/run2/rcpt_20261027_run2_cap350.json"

def main(section_path, name):
    parent = json.loads(PARENT.read_text())
    sec = json.loads(Path(section_path).read_text())
    doc = {"spec": "wattnot.receipt.addendum/1", "receipt_id": f"{parent['receipt_id']}.{name}",
           "parent": {"receipt_id": parent["receipt_id"], "sha256": parent["integrity"]["sha256"]},
           "label": sec.get("label", "LIVE"), "section": name, "data": sec,
           "integrity": {"canonicalization": "JCS RFC 8785", "sha256": "", "signer": None, "rekor": None}}
    doc["integrity"]["sha256"] = canonical_sha256(doc)
    out = PARENT.with_name(f"{PARENT.stem}.{name}.json")
    out.write_text(json.dumps(doc, indent=2) + "\n")
    b = sign(out, no_rekor=True, key_path=ROOT / "verifier/samples/test-key.pem")
    print(out.relative_to(ROOT), b.relative_to(ROOT), doc["integrity"]["sha256"])

if __name__ == "__main__":
    main(*sys.argv[1:3])
