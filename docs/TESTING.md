# Testing

Quick check for the feature you are working on (seconds):

    python -m pytest -q -m "not slow" tests/test_<your_feature>.py
    node tests/js/index.js

Do not run the full Python suite inside a feature session. It takes 7+ minutes because the rehearsal and resume tests (`test_proof_events.py`, `test_resume.py`) take about 20 s each, and they are marked `slow`.

The full suite runs once after each feature as an integration step on the shared branch, and again at the final review before merging into main:

    python -m pytest -q
    node tests/js/index.js

If `tests/test_schemas.py` fails after you change a file in `schemas/`, copy it to `docs/schemas/` and run `python scripts/gen_ts_types.py`.
