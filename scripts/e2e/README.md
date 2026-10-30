# scripts/e2e

`ctl.py` — E2E Networks GPU VM controller (launch / status / delete / balance watchdog). All
credentials are read from the environment (`E2E_API_KEY` / `E2E_API_TOKEN`, via the `post`
helper on the control host) and are never hardcoded or printed; see the module docstring for the
command list and safety rails. The module is import-safe, so its launch-rule helpers can be unit
tested without the `post` helper present (`tests/test_e2e_ctl.py`).

The Glimmer test (3.5 h booking, deleted as soon as the plan ends) that runs on the VM `ctl.py` launches includes the vision quality gate
(the "eye test") as a step with a 90 s wall-clock budget per candidate — see
`plan_glimmer_test` in `wattnot/longrun/driver.py` and the eye-test section of
`docs/REVIEW-1029.md`. It runs offline inside the test window (`python -m wattnot.vision.run
--fake --budget-s 90`); no launch and no paid call happen here. `ctl.py` itself is unchanged:
it only bounds the VM lifetime, so the eye test always fits inside the deadline it enforces.

The same plan also includes the watts-per-spoken-answer gate as a step with its own 90 s budget
per candidate (`python -m wattnot.spokenanswer.run --fake --budget-s 90`). Glimmer answers 20
SYNTHETIC recorded questions (clean + data-center noise) whose transcripts are cached from Muse
Voice Transcribe (one-time paid, never run here), and the block reports joules per correct spoken
answer next to joules per token and joules per correct answer; a wrong spoken answer rejects the
candidate just like a misread eye-test image.

## Before launch: the approval policy (operator, once)

The live sweep never uses `--auto-approve`. It files the kept power cap as a PENDING proposal
with its measured evidence, holds it for 20 s (visible in the dashboard's Guard panel, where
a person can approve or reject), then lets an operator-set policy decide. `ctl.py launch` and the
driver both refuse to start without the policy, before any spend:

    wattnot proposals --dir /tmp/wn-policy policy set --by "<your name>" --file approval-policy.json

The file (repo root, git-ignored) records `set_by` and `set_at`; thresholds default to a saving of
at least 3%, p99 up at most 10%, a cap drop of at most 45%, MEASURED numbers only. The receipt's
`guardrail.approval_policy` block and the approve op note carry "approved by policy, set by
operator <who> at <when>". If a threshold fails, the change stays pending and times out
(= reject, the GPU goes back to stock).

After the sweep, block `t1-eu` runs `wattnot report eu` on the signed sweep receipt (control box,
no GPU time). It checks the receipt's SHA-256 first.
