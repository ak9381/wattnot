# Long-run plan (`--plan long`)

Plan and config only. Building the plan starts nothing; a live run still needs a VM launched by
hand and Akash's go.

Preview it offline:

```
python -m wattnot.longrun.driver --dry-run --model glimmer-test --plan long --out /tmp/long-dry
```

What changed from the 31 Oct rerun:

| Fix | Before | Long plan |
|---|---|---|
| Wonder planner budget (real Wonder, `--wonder-live`, never the mock) | 1500 s sweep budget, 2 min chart read | 2400 s, 6 min |
| GSM8K items | 4-item warm-up check only | all 60 bundled GSM8K test items + 40 HumanEval, 3 reps, best cap vs stock (`t2e-public`) |
| Eye test (BF16 + FP8) | skipped | back in (`t1-eye`, `t2-eye`) |
| Spoken checks (BF16 + FP8) | skipped | back in (`t1-spoken`, `t2-spoken`) |
| FP8 swap | skipped | back in (`t2-fp8`) |
| Batching / KV sweep | skipped | back in (`t2b-batch`) |
| Prefix caching A/B + combined headline | skipped | back in, 3 reps (`t2c-prefix`, `t2d-combined`) |

Time: about 4.6 h of blocks plus the 8 min watchdog margin, booked as one H100 for 5 h
(ESTIMATE). Cost depends on the provider's hourly rate at launch time; the Wonder calls are cents
(ESTIMATE). `tests/test_longrun_long_plan.py` checks the blocks are present, Wonder is live with
the bigger budget, no live command uses `--fake`, and the plan fits the booking.
