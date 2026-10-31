# Gap tests, 2026-10-31 (no GPU, no VM, Muse models only)

Total Meta API spend: $1.54 (MEASURED, spend ledger purposes `gap-1031*`, 677 calls), job cap $3.

| # | Test | Result | Key numbers |
|---|------|--------|-------------|
| 1 | Memory compression, live (Muse Spark summariser) | FAIL (bug) | 9/16 runs got HTTP 400: the middleware drops old assistant tool_calls but keeps their `tool` replies (orphan tool_call_id). 5 more hit network errors; 2/2 clean runs passed. MEASURED |
| 2 | Tool-output dedup, live | PASS, small | 12 clean pairs: prompt tokens -6.0%, uncached prompt -4.7%, completion +4.1%, calls -0.6%, pass 12/12 vs 12/12. MEASURED |
| 3 | Code Mode, fully live (real tools in sandbox) | PASS | 7 clean runs per arm: round trips 3.29 -> 1.00, tokens 3,703 -> 970 per run (-74%), pass 7/7 both, wall 5.69 s -> 6.03 s. Sandbox self-check refused out-of-dir write, subprocess, raw socket, other host. MEASURED |
| 4 | Prefix-cache re-grade | BLOCKED | t2c-prefix saved only the summary, no per-answer text, so it can't be re-graded offline. Fix: save raw answers in the retest. |
| 5 | Eye test on Muse Wonder | 9/10 | Same miss: gauge. Wonder spent 7,998 reasoning tokens on it and returned nothing (budget 8,000). The gauge image is ambiguous (needle between 62 and 63). MEASURED |
| 6 | Sweep re-grade, own final-answer parser | Done | Math: every finished answer correct (100%). All misses are cut answers: ~59-61% of math items finished, 64/64 QA answers cut in every block. Stock vs 375 W: 58.7% vs 60.7% of all math items. MEASURED (re-grade of MEASURED outputs) |
| 7 | Compare UI | Renders, gap | Loads with no console errors, but lists only RUN2 + fixtures: the Oct 31 run cannot be selected. "Spark Trace ID" falls back to a hard-coded placeholder (`v2.js:1221`). |
| 8 | Watchdog, five layers, dry-run, fake VM 999999, `env -i` | 4/5 OK | check, L2, L3, L4, L5 fire correctly on stale / over-budget / past-deadline and say WOULD_DELETE. L1 (`watchdog.local`) uses `time.monotonic` as now, so it never fires on stale or deadline (budget only). L2/L3 check command hardcodes `python`. |

## Draft README wording for MoE adaptive top-k (item 9)

> **Adaptive top-k (research, not a Meta-model result).** The router hook is built for the
> IBM Granite 3.1 3B MoE layout, because the Meta models we use are not served with a router we
> can change. On one H100 (RUN2) it cut expert slots 15% for -6.6% J/token (MEASURED), but token
> agreement was 94.5%, under our 95% gate, so no change was proposed. The 0.95 setting was tuned
> on a CPU (agreement MEASURED on CPU; the -1.5% J/token saving is an ESTIMATE). It is not part
> of the Meta-model results.
