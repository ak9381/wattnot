# Demo replays and EU reports

- **Replays** (`replays/` on main after `feat/demo-replays` merges): four end-to-end replays
  stitched from the final good run of each block. No GPU, no Meta/Baseten call. Every event keeps its
  own label and gets `replay.label = REPLAY` + source run. Index: `replays/INDEX.md`.
  - `modal-r1` (r04f + LangChain rerun; verdict REJECTED on p99 only),
  - `modal-r2` (r05 + LangChain fix + eye/hearing both-arms + attribution arms),
  - `baseten-r1` (r02 full), `baseten-r2` (r03 rerun on the step-up budget fix).
  - Builder `scripts/build_replays.py`; check `pytest tests/test_replays.py`.
  - Play one: `PYTHONPATH=src WATTNOT_META_MOCK=1 python -m wattnot.dash --replay-only --events replays/modal-r2/events.jsonl --port 8795`.
- **Archive** `data/archive/INDEX.md`: superseded/failed runs, README each, nothing moved or deleted.
- **EU reports**: each Modal replay has `eu_report.html` built by `wattnot.report.eu` from the signed
  NVML `energy-receipt.json`; Baseten ones are the page the round wrote (no GPU meter).
  The H100 retest report is `receipts/rerun-1031/eu_report.html` (built from the 31 Oct receipts).
  Rebuild: `python scripts/build_eu_report.py --help`.
- **Receipt panel**: `/api/dash/run/receipt` now shows round and sweep receipts too (summary view,
  numbers copied exactly from the receipt), merged into main as 523ccfb (that branch was deleted after the merge).
- Verify any receipt: `wattnot verify <receipt.json>` (offline Ed25519; Rekor is never used).
