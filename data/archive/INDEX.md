# Archived runs (index only)

Nothing here was moved or deleted. The runs stay where they are on their data branches, so their
signatures (receipt.sigstore.json, attachment.sigstore.json) and every path that points at them still
work. Each folder below holds a README saying why the run is not used in a demo replay.

| Run | Status | Branch | Why | Fixed by | Replaced by |
|---|---|---|---|---|---|
| [modal-r04-aborted-0100](modal-r04-aborted-0100/README.md) | failed | `data/modal-full-1101` | Modal round 1, first launch, aborted 01:00 IST Nov 2: the event recorder crashed. ~$0.125 ESTIMATE. | recorder fix merged before r04 try 1 | r04-try1, then r04f |
| [modal-r04-try1](modal-r04-try1/README.md) | superseded | `data/modal-full-1101` | Modal round 1 try 1 finished rc 0 (J/correct -68.3% MEASURED) but blocks were off: agentwaste timeout (rc 124), eye 63 vs 60, Muse Code PR KeyError, optuna missing. | main c1ca7e7 (tag pre-modal-r05-prep-1102) | r04f |
| [modal-r04b-stopped](modal-r04b-stopped/README.md) | failed | `data/modal-full-1101` | Stopped 04:28 IST by another script's teardown (stopped any wattnot-full-stage app by name). $0.79 ESTIMATE. | owner lock, main 3113970 (tag pre-modal-r04c-1102) | r04c, then r04f |
| [modal-r04c](modal-r04c/README.md) | superseded | `data/modal-full-1101` | Round approved on energy (-63.56% J/correct MEASURED) but LangChain failed (rc 5, saver_on 11/20) and hearing failed (Meta key not in the container). | Meta key secret: main 175509e; LangChain saver: 6101971, e3a0415, 7dcbf6f | r04f |
| [modal-r04d](modal-r04d/README.md) | failed | `data/modal-full-1101` | Container crash-looped at start (Modal secret deps mismatch). No results. | main 175509e (fix/modal-secret-1102) | r04e, then r04f |
| [modal-r04e](modal-r04e/README.md) | rejected | `data/modal-full-1101` | Rejected: accuracy -3.13 pts, p99 77.6 -> 498.7 s, 8 complex agent items regressed, LangChain saver 5/20. | LangChain saver fixes 6101971, e3a0415, 7dcbf6f; incoming-dir gate 38f6527 | r04f |
| [modal-r04e-langchain](modal-r04e-langchain/README.md) | superseded | `data/modal-full-1101` | Block-only LangChain reruns attached to r04e. The first died on a missed heartbeat; the second passed but belongs to the rejected r04e round. | detached block runs (fix/detach-block-run); fc-tune 95b4431 | r04f-langchain-20261102T112336Z (modal-r1) |
| [modal-r04f-own-langchain](modal-r04f-own-langchain/README.md) | superseded | `data/modal-full-1101` | r04f's own LangChain pass (rc 0) ran before fc-tune; the block was rerun after it. | fc-tune, main 95b4431 | r04f-langchain-20261102T112336Z (modal-r1) |
| [modal-r05-own-langchain](modal-r05-own-langchain/README.md) | superseded | `data/modal-full-1101` | r05's own LangChain pass ran before the short-conversation bypass fix. | db7f925 (fix/langchain-short-bypass) | r05-langchain-20261102T140740Z (modal-r2) |
| [modal-r05-post-round-eye-hearing](modal-r05-post-round-eye-hearing/README.md) | superseded | `data/modal-full-1101` | Post-round eye/hearing results were MOCK copies (offline rerun), and eye/hearing ran on the WattNot server only. | fix/eye-hearing-energy cb78c26, fix/eye-hearing-block-arms 6da43a5 + 3002017 | r05-eye-hearing-20261102T153519Z (modal-r2) |
| [modal-smokes](modal-smokes/README.md) | smoke only | `data/modal-full-1101, data/a100-smoke-1101` | Dry-run and UI smokes on A100, not a round. | n/a | not replayed |
| [baseten-r01-smokes](baseten-r01-smokes/README.md) | smoke only | `main, data/a100-smoke-1101` | Nov 1 Baseten smokes (limit 3), not a round. | n/a | baseten-r1 uses r02 full |
| [baseten-r02-smoke](baseten-r02-smoke/README.md) | smoke only | `data/baseten-1102-smoke (in data/modal-full-1101 history)` | Smoke limit 3, 6/6 both arms; usage crosscheck false (compared only arm calls). | 4595a6b (fix/baseten-usage-count) | r02 full (baseten-r1) |
| [baseten-r03-own](baseten-r03-own/README.md) | superseded | `data/baseten-1102-own-e2e` | Main round latency verdict FAIL (with p95 41.6 s, p99 48.7 s; 8 wasted step-ups) and Spark trace 0/4 (hidden reasoning used the whole 1500-token cap). Its LangChain block is still used. | a408281 (fix/stepup-projected-budget) | data/baseten-1102-own-rerun @ 4ad8cff (baseten-r2) |

Not archived and not in a replay: the H100 retest (`data/h100-rerun-1031`, `receipts/rerun-1031`), a different
machine and story; the stage branches `data/baseten-stage-1101` and `data/modal-stage-1101` (stage code snapshots).
