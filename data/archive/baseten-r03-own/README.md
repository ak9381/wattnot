# baseten-r03-own

Status: superseded

Where it lives: branch `data/baseten-1102-own-e2e`, `data/baseten-1102-own/r03, events/` (not moved; signatures untouched).

Why: Main round latency verdict FAIL (with p95 41.6 s, p99 48.7 s; 8 wasted step-ups) and Spark trace 0/4 (hidden reasoning used the whole 1500-token cap). Its LangChain block is still used.

Fixed by: a408281 (fix/stepup-projected-budget)

Replaced by: data/baseten-1102-own-rerun @ 4ad8cff (baseten-r2)
