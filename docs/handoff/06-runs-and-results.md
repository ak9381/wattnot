# Run history and MEASURED results

The full, authoritative list is **`docs/RUN_HISTORY.md`** in the repo (every paid run, original
verdict kept as recorded, cause, fix commit, next result, REPLAY re-scores). Raw data is on the
`data/*` branches (see 05-branches.md). Highlights, copied from it:

| Run | When (IST) | Where | Cost | Verdict | Headline |
|---|---|---|---|---|---|
| H100 retest | 31 Oct | E2E H100 | ~₹1,571 ESTIMATE | 375/350/325 W pass, 300 W fail p99 | 325 W + prefix cache: **-44.1% J/correct**, TTFT -84.4%, p99 -12.3%, pass 100% (MEASURED) |
| A100 smokes 4/5 | 1 Nov | Modal A100 | $1.24 / $0.99 MEASURED | held | smoke only |
| Baseten smokes | 1-2 Nov | Baseten | $0.21 / $0.30 MEASURED | rejected | fixed step-up, hearing clips, gauge |
| Modal r04c | 2 Nov 5:53-7:40 AM | Modal A100 | $4.87 MEASURED | **approved** (2 blocks failed) | -63.6% J/correct (MEASURED) |
| Modal r04e | 2 Nov 9:13-11:31 AM | Modal A100 | $6.02 + $0.39 Meta MEASURED | rejected | context saver broke long agent chats, -23.9% |
| Modal r04f | 2 Nov 2:44-4:26 PM | Modal A100 | $4.42 MEASURED | **rejected on p99 only** (88.4 -> 104.6 s) | **-67.6% J/correct** (MEASURED) |
| Modal r05 + reruns, Baseten r02/r03 | 2 Nov afternoon/evening | Modal / Baseten | see RUN_HISTORY | see replays INDEX | base of replays modal-r2, baseten-r1/r2 |

Rules when quoting: keep the label, quote the original verdict, never round into a better claim.
Baseten J numbers are ESTIMATE (no GPU meter on a hosted API).
