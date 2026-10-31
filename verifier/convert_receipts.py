#!/usr/bin/env python3
"""convert_receipts.py: turn WattNot's receipt folder into a pack the judge verifier checks.

Standard library only, like wattnot_verify.py next to it. No network, no signing, no Rekor.

    python verifier/convert_receipts.py receipts/ --out judge-pack/

WattNot writes three kinds of JSON under receipts/:

  receipt    spec wattnot.receipt/1 with a .sigstore.json bundle. The verifier reads these as
             they are, so they are copied unchanged (same bytes, signature still valid).
  addendum   spec wattnot.receipt.addendum/1: a signed extra section (Wonder trace, voice,
             SAM, Muse Code PR) pointing at a parent receipt by id and sha256. The judge
             verifier only grades full receipts, so the converter checks each addendum here:
             its JCS sha256, its Ed25519 signature, and that its parent is in the pack with the
             same sha256. Its bytes are not changed (changing them would break the signature).
  other      anything else. Raw inputs and test logs are copied as EVIDENCE. Other receipt
             specs (code-mode receipts) are copied and flagged NEEDS RE-ISSUE:
             turning them into wattnot.receipt/1 would mean inventing baseline/candidate arms
             and re-signing, which this tool never does.

The pack gets a MANIFEST.json: every file, its kind, sha256 of its bytes, the verifier's grade
for receipts and the addendum checks, so a judge can run

    python verifier/wattnot_verify.py judge-pack/receipts/<id>.json

and cross-check the rest by hash.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wattnot_verify as wv  # noqa: E402

ADDENDUM = "wattnot.receipt.addendum/1"


def classify(doc) -> str:
    if not isinstance(doc, dict):
        return "other"
    if doc.get("spec") == wv.SPEC:
        return "receipt"
    if doc.get("spec") == ADDENDUM:
        return "addendum"
    return "other"


def _bundle_for(p: Path) -> Path:
    return p.with_name(p.stem + ".sigstore.json")


def check_addendum(doc, bundle, parents: dict) -> dict:
    sha_ok = wv.receipt_sha256(doc) == (doc.get("integrity") or {}).get("sha256")
    sig_ok, sig = wv._signature(doc, bundle, None)
    par = doc.get("parent") or {}
    have = parents.get(par.get("receipt_id"))
    parent_ok = have is not None and have == par.get("sha256")
    return {"sha256_ok": sha_ok, "signature_ok": sig_ok, "signer": sig.get("signer"),
            "signature_error": sig.get("error"), "parent": par.get("receipt_id"),
            "parent_ok": parent_ok,
            "status": "ATTACHED" if sha_ok and sig_ok and parent_ok else "BROKEN"}


def convert(src: Path, out: Path, trusted=None) -> dict:
    src, out = Path(src), Path(out)
    files = sorted(p for p in src.rglob("*.json") if not p.name.endswith(".sigstore.json"))
    docs = {}
    for p in files:
        try:
            docs[p] = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            docs[p] = None
    parents = {d["receipt_id"]: (d.get("integrity") or {}).get("sha256")
               for d in docs.values() if classify(d) == "receipt"}
    entries = []
    for p, d in docs.items():
        kind = classify(d)
        rel = p.relative_to(src)
        dest = out / {"receipt": "receipts", "addendum": "addenda", "other": "evidence"}[kind] / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
        b = _bundle_for(p)
        bundle = None
        if b.exists():
            shutil.copy2(b, _bundle_for(dest))
            bundle = json.loads(b.read_text(encoding="utf-8"))
        e = {"file": str(dest.relative_to(out)), "source": str(p), "kind": kind,
             "bytes_sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
             "bundle": b.exists()}
        if kind == "receipt":
            r = wv.verify_path(dest, trusted=trusted)
            e["grade"] = r["grade"]
            e["receipt_id"] = d.get("receipt_id")
        elif kind == "addendum":
            e.update(check_addendum(d, bundle, parents))
        elif not (isinstance(d, dict) and d.get("spec")):
            e["status"] = "EVIDENCE"
            e["why"] = "raw input or test log, cross-checked by hash"
        else:
            e["status"] = "NEEDS RE-ISSUE"
            e["why"] = ("not a wattnot.receipt/1 receipt; converting it would need new "
                        "baseline/candidate arms and a new signature, which this tool never does")
        entries.append(e)
    manifest = {"format": "wattnot.judge-pack/1", "source": str(src),
                "note": "Receipts copied byte for byte. Nothing signed, nothing published.",
                "files": entries}
    out.mkdir(parents=True, exist_ok=True)
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src", help="receipts folder")
    ap.add_argument("--out", required=True, help="output folder for the judge pack")
    ap.add_argument("--trust", help="trusted-keys.json (default: the one beside the verifier)")
    a = ap.parse_args(argv)
    m = convert(Path(a.src), Path(a.out), trusted=wv.load_trusted(a.trust))
    for e in m["files"]:
        tag = e.get("grade") or e.get("status")
        print(f"{e['kind']:9} {tag:15} {e['file']}")
    print(f"MANIFEST: {Path(a.out) / 'MANIFEST.json'}")
    return 0 if all(e.get("status") != "BROKEN" for e in m["files"]) else 1


if __name__ == "__main__":
    sys.exit(main())
