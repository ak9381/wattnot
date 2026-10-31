# Judge pack: receipts the standalone verifier can check

```
wattnot receipt convert receipts --out judge-pack
# or, with no WattNot install:
python verifier/convert_receipts.py receipts --out judge-pack
python verifier/wattnot_verify.py judge-pack/receipts/rerun-1031/rcpt_20261031_rerun_cap325.json
```

- **Receipts** (`wattnot.receipt/1`) are copied byte for byte with their signature bundles and
  graded by the verifier. Both current receipts grade ESTIMATE: they are signed with the offline
  test key and are not on Rekor yet.
- **Addenda** (Wonder trace, voice, SAM hotspot, Muse Code PR, quality definition) are checked for
  hash, signature and parent link, and marked ATTACHED or BROKEN. Their bytes are not changed.
- **Evidence** (raw live outputs, framework test logs) is copied and listed by hash.
- **NEEDS RE-ISSUE** marks other receipt formats (code-mode). The converter does not invent
  fields or re-sign anything; re-issuing them is a separate, signed step.

`MANIFEST.json` lists every file, its kind, sha256 and result. Nothing is signed or published.
