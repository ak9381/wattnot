# modal-r04f

Status: rejected (safety catch)

Where it lives: branch `data/modal-full-1101`, `data/modal-full-1101/r04f (also r04f.*.events.jsonl)` (not moved; signatures untouched).

Why: The energy saving was real (-67.6% J/correct, MEASURED), but p99 latency went from 88.4 s to 104.6 s (MEASURED), past the 10% limit. WattNot refused the change, which is what the gate is for.

Fixed by: step-up latency guard, main 0da21ff (Modal round 2, r05, is the post-fix round)

Replaced by: modal-r1 replay is now built on r04c; r04f stays here as the recorded rejection.
