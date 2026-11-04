# Hackathon rules: what's not allowed, and how we checked

This file tracks every rule the Meta hackathon organizers told us about, where we heard it, and how we verified that WattNot follows it. Add a row whenever a new rule shows up, and re-check before submitting.

| # | Rule (not allowed / required) | Source | Date heard | How we checked | Status |
|---|---|---|---|---|---|
| 1 | No self-trained model (neural net or other, however small) may run at runtime | Organizer on hackathon Discord | 2026-11-01 | Searched runtime code on main (`src/`) for scikit-learn, xgboost, `.fit(`, `nn.Module`, joblib and pickle files. The only matches are Meta's own open models: a softmax in `moe/hf_backend.py` and a vLLM expert-pinning patch for Spark Open. The router, gates and power caps are plain rules and measurements. | Verified 2026-11-01 |
| 2 | Use Meta models only | Hackathon brief | 2026-10-26 | Every model call goes to Meta models (Muse Spark/Wonder, SAM 3.1, Meta open weights). | Verified, re-check before submit |
| 3 | No VPN or other way around region limits | Hackathon brief / region limits | 2026-10-26 (updated 2026-11-04) | Muse Image was blocked in India early on; Akash confirmed on Nov 4 that it now works. It is used only for the pre-generated SYNTHETIC test images; no run makes a live call. | Verified, re-check before submit |
| 4 | Judges need an open replay or mock demo path that works without paid keys | Organizer on hackathon Discord | 2026-11-01 | Replay mode exists on the dashboard. Still to confirm it runs cleanly from a fresh clone. | Open |

## Re-check commands

```
git grep -nIl -E "sklearn|xgboost|lightgbm|\.fit\(|torch\.nn|nn\.Module|joblib|\.pkl" origin/main -- src
```
