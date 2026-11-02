# Repo code map (`ak9381/wattnot`)

| Path | What it is |
|---|---|
| `src/wattnot/cli.py` | `wattnot` CLI: `init`, `verify`, `preflight`, `dash`, `report`, `receipt`, `eu`, etc. |
| `src/wattnot/planner/`, `spark_open/`, `reasoning.py` | Muse Spark planner: proposes one change at a time |
| `src/wattnot/gates/` | quality / p99 / Welch-noise gates |
| `src/wattnot/control/`, `meter/`, `hostinfo.py` | power caps, vLLM knobs, NVML energy metering |
| `src/wattnot/receipt/`, `certificate/`, `verify.py` | hash-chained signed receipts, certificate, verifier |
| `src/wattnot/report/` | EU efficiency report (HTML) builder |
| `src/wattnot/longrun/` | H100 long-run `driver.py` (blocks g0..g9, dry-run FakeVm), `matrix_round` |
| `src/wattnot/e2e/` | live round plumbing: `fullstack.py` (levers, feature tables), `live.py` (event log, billing), `latency_guard.py` |
| `src/wattnot/proof/` | orchestration on VMs/Modal/Baseten (`orchestrate.py`, `modal_provider.py`, `baseten_planner.py`, `transport.py`) |
| `src/wattnot/context_saver/`, `agentwaste/`, `flex/`, `route/`, `moe/` | savings levers: context saver, agent waste guard, flex scheduling, routing |
| `src/wattnot/vision/`, `sam/`, `spokenanswer/` | eye gate (Wonder/SAM), hearing gate (Voice Transcribe) |
| `src/wattnot/meta/`, `musecode/` | Meta API client, mock, spend guard; Muse code snippets |
| `src/wattnot/approvals/`, `spend/`, `launch_check.py`, `preflight.py` | approval policy, spend caps, pre-launch checks |
| `src/wattnot/dash/` | dashboard server (`server.py`, `receiptview.py`), replay mode |
| `web/dash/` | dashboard pages (index, run, compare, report, receipts, graph, help on UI branch ...) and JS (`chrome.js`, `bento.js`, `sidenav.js`, `product.js`) |
| `modal/` | Modal apps (`full_stage.py` holds `META_SECRET = "wattnot-meta"`) |
| `scripts/e2e/` | round runners: `modal_round.py`, `modal_block_run.py`, `modal_stage.py`, `modal_full_round.py`, `baseten_round.py`, `preflight.py`, `ctl.py`, `round.py` |
| `scripts/` | `glimmer_serve.sh`, `h100_*.sh`, `build_eu_report.py`, `build_replays.py` (replays branch), benches |
| `receipts/` | committed receipts (`rerun-1031/` H100, `run2/`, ...) |
| `replays/`, `data/archive/` | demo replays and archive READMEs (from `feat/demo-replays`) |
| `docs/` | `RUN_HISTORY.md` (every paid run), `runbooks/`, `JUDGE-PACK.md`, `WALKTHROUGH.md`, `TESTING.md`, `handoff/` (UI code lives in `web/` and `src/wattnot/dash/`) |
| `tests/` | pytest suite (~12 min full) + JS tests |
