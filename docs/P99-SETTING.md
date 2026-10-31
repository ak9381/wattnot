# One p99 latency limit

p99 is the response time that 99 of 100 requests beat. WattNot has one user setting for how much
worse it may get after a change: `max_p99_increase`, a fraction, default `0.10` (10%).

Set it in `wattnot.json` (or `~/.wattnot/config.json`, or the file named by `$WATTNOT_CONFIG`):

```json
{"max_p99_increase": 0.10}
```

or for one shell with `WATTNOT_MAX_P99_INCREASE=0.05`. `"5%"` also works.

Every layer reads it from `wattnot.settings`:

| Layer | Where | How |
|---|---|---|
| GPU sweep (caps, clocks) | `gates/latency.p99_gate`, `gates/decide` | candidate p99 must be at most baseline × (1 + limit) |
| Open-model checks | `spark_open/gate`, `spark_open/tune` | same rule against the reference p99 |
| Router | `spark_open/router` | a cheaper backend counts as a tie only if its p99 is within the limit of the fastest |
| Cloud tool | `wattnot cloud tune --p99-slack` | the flag is an alias that overrides the setting for one run |

The setup screen shows it next to the auto-approve rule (cap may drop at most 58%). Every new
receipt has a `limits` block with `max_p99_increase`, its percent form and where it came from
(`default`, `config:…`, `env:…`, `override`). Older receipts have no `limits` block and still
validate.
