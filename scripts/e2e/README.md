# scripts/e2e

`ctl.py` — E2E Networks GPU VM controller (launch / status / delete / balance watchdog). All
credentials are read from the environment (`E2E_API_KEY` / `E2E_API_TOKEN`, via the `post`
helper on the control host) and are never hardcoded or printed; see the module docstring for the
command list and safety rails. The module is import-safe, so its launch-rule helpers can be unit
tested without the `post` helper present (`tests/test_e2e_ctl.py`).
