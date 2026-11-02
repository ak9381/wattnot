# Box preflight before the 3 paid Modal A100 rounds

Checked 1 Nov 2026, about 11:30 to 11:50 PM IST. Read-only, no paid job launched, nothing spent.

| Check | Result | Detail |
|---|---|---|
| Disk / , /workspace, /tmp | PASS | Same volume: 81 GB free of 128 GB (37% used). /tmp holds 2.5 GB. |
| Memory and swap | PASS | 15 GiB total, 7.8 GiB available. No swap (normal for the box). |
| CPU load | PASS | Load about 1.0 on 8 cores. |
| Stray processes | WARN | No vLLM, no orphan launchers. One demo server (dashboard on 8770). One stuck `agy` council call (PID 1563248) running for 26 h despite `timeout 900`; it uses no CPU. Left alone for Akash to decide. |
| Modal auth | PASS | `modal token info` ok, workspace akashkraju218, ~2 s. |
| Modal apps | PASS | `modal app list`: no running or deployed apps; last wattnot-full-stage app stopped 11:18 PM IST. |
| Results volume wattnot-results | PASS | Reachable; holds smoke-smoke-ui1645 and smoke-smoke-ui1728. |
| GitHub | PASS | api.github.com 200 (1.6 s); `git ls-remote` ok. |
| Baseten (paid key) | PASS | Inference /v1/models 200 (2.2 s), lists meta-models/muse-spark-open. |
| Meta API | PASS | GET /v1/models 200 (0.4 s). |
| Dashboard 8770 | PASS | HTTP 200; runs from the /tmp/wn-merge checkout at 0702a3d = origin/main. |
| Env keys present (names only) | PASS | BASETEN_METRICS_KEY, BASETEN_API_KEY, HF_TOKEN in env; Meta key via `load_key()`; Modal token in ~/.modal.toml. |
| Caps | PASS | Round $10 (modal/full_stage.py ROUND_HARD_USD), smoke $1.50, stage $60 (scripts/e2e/modal_full_round.py), Baseten $8/round (scripts/e2e/baseten_round.py), Meta $120 hard / $100 soft (src/wattnot/meta/ledger.py). |
| stage_spend.json | PASS | Sum of 12 rounds = $5.2024 = recorded total (ESTIMATE). |
| Git checkouts clean | WARN | /tmp/wn-merge (dashboard): stage_spend.json modified and smoke result dirs untracked, i.e. run data not committed. /workspace/wn-h100e2e has 4 changes (CPU-fix worker, in progress). /workspace/wattnot is an old feature branch with 28 changes. Not touched. |
| Screen recording | PASS | ffmpeg x11grab 5 s test on :3 produced a 5.0 s clip; test file deleted. |
| Huge logs | PASS | No log over 200 MB. |

## Notes
- The docstring in src/wattnot/meta/ledger.py `caps_from_env` still says "$100 / $80"; the constants are the approved $120 / $100. Cosmetic only.
- src/wattnot/gates/judge.py builds a BasetenClient with cap_usd=98.0. Worth checking that the $8/round Baseten stop still applies when the judge runs.
- The 1 Nov smoke run data in /tmp/wn-merge should be committed to the data branch before the rounds so it isn't lost.
