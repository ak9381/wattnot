# scripts/e2e

`ctl.py` — E2E Networks GPU VM controller (launch / status / delete / balance watchdog). All
credentials are read from the environment (`E2E_API_KEY` / `E2E_API_TOKEN`, via the `post`
helper on the control host) and are never hardcoded or printed; see the module docstring for the
command list and safety rails. The module is import-safe, so its launch-rule helpers can be unit
tested without the `post` helper present (`tests/test_e2e_ctl.py`).

The 2h Glimmer test that runs on the VM `ctl.py` launches includes the vision quality gate
(the "eye test") as a step with a 90 s wall-clock budget per candidate — see
`plan_glimmer_test` in `wattnot/longrun/driver.py` and the eye-test section of
`docs/REVIEW-1029.md`. It runs offline inside the test window (`python -m wattnot.vision.run
--fake --budget-s 90`); no launch and no paid call happen here. `ctl.py` itself is unchanged:
it only bounds the VM lifetime, so the eye test always fits inside the deadline it enforces.
