# M01 TDD: probe.py and host capability check
Branch: `feat/m01-probe` · Hours: 3 · Depends on: none

## Interfaces
```
python -m wattnot.probe [--out capabilities.json] [--no-write] [--pre-approved NAME]
# --no-write = read-only check. The write test lowers the cap and puts it back, so it asks for
# approval first (docs/APPROVALS.md): --pre-approved NAME, or `wattnot proposals approve <id>`.
exit 0 = probe ran (even if caps are blocked); exit 2 = no GPU / NVML missing;
exit 50 = the write test was not approved (nothing was changed)
```
`capabilities.json`:
```json
{"host":{"provider":"e2e","gpu":"H100 80GB","driver":"…","cuda":"…","vllm":"…"},
 "nvml_read":true,"energy_counter":true,"read_hz_achieved":10.2,
 "power_cap_write":{"ok":true,"min_w":200,"max_w":700,"default_w":700},
 "clock_lock":{"ok":false,"error":"Insufficient Permissions"},
 "mode":"root","probed_at":"2026-10-27T05:00:00Z"}
```

## Data schemas
Uses the canonical run event (`schemas/run-event.schema.json`) and receipt (`schemas/receipt.schema.json`). Module-local formats are defined under Interfaces. Never add fields to the canonical schemas inside this module; open a schema task instead.

## File layout (this module owns only these paths)
`wattnot/probe.py`, `wattnot/hostinfo.py`, `tests/test_probe.py`, `tests/fixtures/nvml_fake.py`

## Test plan (tests first; write, watch fail, then pass)
Unit tests with a fake NVML (cap write allowed / denied / NVML missing). Restore-on-exception test: cap is reset even if the process is killed by SIGTERM (try/finally + signal handler). Live smoke on Modal (container, expect write=false) is a manual task.

## Acceptance criteria
Runs on Modal and the rental; produces valid JSON; never leaves cap or clocks changed (checked by reading them back); runtime < 10 min.
- `make test` green, `ruff`/`tsc` clean, and the branch pushed.

## Dependencies
none

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
