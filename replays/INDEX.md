# Demo replays

Four end-to-end replays stitched from the final good run of every block. Replay only:
no GPU, no Meta or Baseten call. Every event keeps its own label (MEASURED, ESTIMATE, MOCK)
and carries `replay.label = REPLAY` plus the source run. Built by `scripts/build_replays.py`;
superseded and failed runs are listed in `data/archive/INDEX.md`.

| Replay | What | Base run (commit) | Replaced blocks | Gaps | Launch |
|---|---|---|---|---|---|
| [modal-r1](modal-r1/manifest.json) | Modal round 1 (A100, public gsm8k+humaneval): r04c (approved), LangChain and eye+hearing from the later fixed block runs | r04c on data/modal-full-1101 (3041ebd) | langchain: r05-langchain-20261102T140740Z (data/modal-r05-langchain-fix d9111ad)<br>eye+hearing: r05-eye-hearing-20261102T153519Z (data/modal-r05-eye-hearing 521c71c) | The LangChain and eye+hearing blocks were run on 2 Nov after r04c, attached to round r05 (same A100 setup, same Glimmer 30B server image). They are not from the r04c container itself.<br>r04c's receipt.json and energy receipt are copied as recorded: they still count r04c's failed LangChain and hearing blocks. The replaced block results carry their own signed attachments.<br>r04f (the later Modal round 1, REJECTED on p99 only, 88.4 -> 104.6 s, MEASURED) is kept in data/archive/INDEX.md, not hidden. | `python -m wattnot.dash --replay-only --events replays/modal-r1/events.jsonl` |
| [modal-r2](modal-r2/manifest.json) | Modal round 2 (A100, public gsm8k+humaneval): r05, LangChain fix rerun, eye+hearing both-arms rerun, attribution arms | r05 on data/modal-full-1101 (3041ebd) | langchain: r05-langchain-20261102T140740Z (data/modal-r05-langchain-fix d9111ad)<br>eye+hearing: r05-eye-hearing-20261102T153519Z (data/modal-r05-eye-hearing 521c71c)<br>attribution: r05-ablate-20261102T145207Z (data/modal-r05-attribution c66bc9b) | none | `python -m wattnot.dash --replay-only --events replays/modal-r2/events.jsonl` |
| [baseten-r1](baseten-r1/manifest.json) | Baseten round 1 (Muse Spark open, public gsm8k+humaneval): r02 full | r02 on data/baseten-1102-full (9bc7662) | langchain: r02-langchain (data/baseten-1102-langchain 102a9b5) | The LangChain block wrote no events file; the replay shows one summary event built from its langchain_pass.json (numbers and label copied as recorded). | `python -m wattnot.dash --replay-only --events replays/baseten-r1/events.jsonl` |
| [baseten-r2](baseten-r2/manifest.json) | Baseten round 2 (Muse Spark open, own set): r03 rerun on the step-up budget fix | r03-rerun on data/baseten-1102-own-rerun (4ad8cff) | langchain: r03-langchain (data/baseten-1102-own-e2e 0be15e3) | LangChain is from r03, not the rerun: it ran before the step-up budget fix (a408281). The fix changes main-round step-ups only.<br>The LangChain block wrote no events file; one summary event is built from its langchain_pass.json. | `python -m wattnot.dash --replay-only --events replays/baseten-r2/events.jsonl` |

Check one: `python -m pytest tests/test_replays.py`.
Receipts: `receipt.json` is the round's signed receipt (offline Ed25519, no Rekor), copied
byte for byte. Modal replays also carry `energy-receipt.json` (signed NVML sweep receipt);
their `eu_report.html` is generated from it by `wattnot.report.eu`. Baseten is a hosted API
with no GPU meter, so its `eu_report.html` is the page the round wrote from its receipt.
