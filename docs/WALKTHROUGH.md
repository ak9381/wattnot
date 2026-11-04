# WattNot walkthrough (Akash's own review)

This is the path through the whole framework, in order: set up on the Mac, open the dashboard, run the one-command flow, replay the H100 rerun, verify a receipt, roll back if needed, and log UI notes. Every command below was run on the build box on Nov 1, 2026 in mock mode (no paid calls, no GPU). Where something did not work, it says so.

Repo on your Mac: `~/Developer/wattnot`. Branch: `main` (`5784f30` or later).

---

## 0. Every feature we have built

Source: `wattnot-private/docs/FEATURE-INVENTORY.md` (every row, all sections), plus the Nov 1 afternoon additions at the bottom. "Label" is the evidence label the row carries. "Where to see it" is the dashboard page or CLI command; when there is no UI for it yet, it lists the code path.

| # | Section | Feature | Status | Label | Where to see it |
|---|---|---|---|---|---|
| 1 | Lead differentiator | One verifiable receipt for energy + carbon + water (SCI-formatted) — lead differentiator | Wh / kWh per correct answer MEASURED (NVML, real GPU runs);  | MEASURED / ESTIMATE / NOT_AVAILABLE | EU card on /eu.html; `wattnot verify` |
| 2 | Master feature table | Power-cap sweep + joules/correct gate | built+tested live (ran once on a rented H100); default/CI pa | MEASURED | /v2.html#pHome; /runs.html |
| 3 | Master feature table | Simulated GPU power model | built+tested (as a model) | ESTIMATE / SIMULATED | /v2.html#pHome; /runs.html |
| 4 | Master feature table | NVML capability probe (read + cap/clock write test) | built, tested against a fake NVML only | MEASURED / SYNTHETIC / MOCK | /v2.html#pHome; /runs.html |
| 5 | Master feature table | FP8 quantization keep/reject gate | built+tested live (e2e) | MEASURED | `src/wattnot/longrun/harness.py`, `src/wattnot/receipt/build.py` |
| 6 | Master feature table | Thinking-budget enforcer (per-tier) | built+tested (fake/MOCK in CI); live path needs an H100 | MOCK | `src/wattnot/longrun/thinking_budget.py` |
| 7 | Master feature table | MoE adaptive top-k (experts-per-token) | planned / built untested (GO-gated) | MEASURED / ESTIMATE | `modal/spark_topk_sprint.py` |
| 8 | Master feature table | Flex grid-event load shedding | SimBackend built+tested as a simulator; hardware path built  | SIMULATED | `wattnot flex` |
| 9 | Master feature table | Fleet impact estimator | built+tested | ESTIMATE | `src/wattnot/impact.py`, `src/wattnot/headline.py` |
| 10 | Master feature table | Model fit/placement + per-model joules | built+tested | MEASURED / ESTIMATE | `src/wattnot/models.py`, `src/wattnot/receipt/per_model.py` |
| 11 | Master feature table | Tool-output dedup middleware | built+tested live (gap test 2) | MEASURED / ESTIMATE | `src/wattnot/context_saver/middleware.py` |
| 12 | Master feature table | Memory-compression middleware | fixed on main; live Spark summariser added (unit-tested, not | MEASURED | `src/wattnot/context_saver/middleware.py` |
| 13 | Master feature table | Prefix-cache A/B harness | built; CI MOCK; live run's gate failed | MEASURED / MOCK | `src/wattnot/context_saver/prefix_cache.py` |
| 14 | Master feature table | Code Mode + locked sandbox | built+tested live (gap test 3) | MEASURED | `src/wattnot/agentwaste/sandbox/runner.py`, `data/agent-waste/codemode/` |
| 15 | Master feature table | Reasoning-effort / cheapest-first router | built+tested mock/replay | MEASURED | `wattnot route`; flow step multiprovider |
| 16 | Master feature table | Noise gate (billability) | built+tested live | MEASURED | `src/wattnot/gates/noise.py` |
| 17 | Master feature table | Fixed answer/quality gate | built+tested | MEASURED | `src/wattnot/gates/fixed.py` |
| 18 | Master feature table | LLM judge gate (pairwise) | built+tested mock/replay (live use evidenced) | MEASURED | `src/wattnot/gates/judge.py` |
| 19 | Master feature table | Reasoning-leak strip | built+tested | n/a | `src/wattnot/proof/client.py` |
| 20 | Master feature table | Grading-label honesty lint | built+tested | MEASURED / SIMULATED / MOCK | `src/wattnot/receipt/{grade,lint}.py` |
| 21 | Master feature table | Code-sandbox network boundary | built+tested live (gap test 3) | MEASURED | `src/wattnot/agentwaste/sandbox/runner.py` |
| 22 | Master feature table | Approve-before-apply + risk classifier | built+tested | n/a | `src/wattnot/approvals/cli.py` |
| 23 | Master feature table | Muse Wonder Pareto planner/supervisor | built+tested, mock by default / live-capable | MOCK / LIVE | `src/wattnot/meta/wonder.py` |
| 24 | Master feature table | Wonderchart (Wonder reads the chart) | built+tested, replay/fallback by default | ESTIMATE / SYNTHETIC | `src/wattnot/wonderchart/{chart,reader,run}.py` |
| 25 | Master feature table | SAM 3.1 rack/room/hotspot audit | built; ran live exactly once; default replay | MEASURED / SIMULATED | /graph.html Meta strip; flow step sam |
| 26 | Master feature table | Muse Spark hosted planner + effort router | built+tested mock/replay | n/a | /graph.html Spark trace panel |
| 27 | Master feature table | Muse Spark Open (served MoE) | used live in hosted A/B; local serving not run (too large) | MEASURED | /graph.html Spark trace panel |
| 28 | Master feature table | Muse Glimmer 30B (served main model) | built; ran live in e2e | MEASURED / MOCK | `src/wattnot/models.py`, `src/wattnot/longrun/harness.py` |
| 29 | Master feature table | Muse Code "apply change as a PR" | built; live call made once (nothing deployed) | ESTIMATE / LIVE | `src/wattnot/musecode/apply_pr.py` (`apply_pr`; real PR only to `ak9381/wattnot-demo-deplo |
| 30 | Master feature table | Muse Voice Transcribe (spoken-answer energy) | built; spoken blocks FAILED live in e2e | SYNTHETIC | Ask box mic |
| 31 | Master feature table | Voice guide (grounded intent) | main: MOCK. `feat/ask-live` (`4c7be36`, not merged): LIVE | MEASURED / MOCK / REPLAY | Ask box mic |
| 32 | Master feature table | Tuner (spoken → safe knob plan) | built+tested | n/a | `src/wattnot/meta/tuner.py` |
| 33 | Master feature table | Muse Image (eighth Meta hero: vision-gate and SAM test sets) | pre-generated assets only; never a live call (blocked in Ind | SYNTHETIC | /graph.html Meta strip; flow step sam |
| 34 | Master feature table | Signed receipt + canonicalization | built+tested live (offline path) | MEASURED / ESTIMATE | `wattnot verify`; /v2.html#pProof |
| 35 | Master feature table | Independent stdlib verifier + forge vectors | built+tested live (executed) | MEASURED | `verifier/wattnot_verify.py`, `verifier/vectors/*` |
| 36 | Master feature table | Joules per good answer | built+tested | MEASURED / SIMULATED | `src/wattnot/receipt/joules.py` |
| 37 | Master feature table | EU efficiency report (Reg 2024/1364) | built+tested | MEASURED / SYNTHETIC / NOT_AVAILABLE | /eu.html; `wattnot report eu` |
| 38 | Master feature table | Cloud effort-router receipt + per-call audit | built+tested live | MEASURED / ESTIMATE / NOT_AVAILABLE | `wattnot route`; flow step multiprovider |
| 39 | Master feature table | Hosted A/B (two providers) | built+tested live ("ran") | MEASURED / ESTIMATE | `src/wattnot/spark_open/hosted_ab.py` |
| 40 | Master feature table | Mock efficiency certificate | built+tested | MOCK | `wattnot certificate` |
| 41 | Master feature table | Recommend / health / runbook / rehearse / demo | built+tested (rehearse/demo mock-only) | n/a | `src/wattnot/{recommend,health,runbook,rehearse,demo}.py` |
| 42 | Master feature table | Static dashboard shell | built+tested | MEASURED | /v2.html |
| 43 | Master feature table | Live run view (3 mini-views) | built+tested mock/replay | REPLAY | `web/dash/liveviews.js`, `app.js`, `replayclock.js` |
| 44 | Master feature table | Expert / orchestra view | built+tested mock/replay | MEASURED | `web/dash/{core,orchestra,expertstrip}.js` |
| 45 | Master feature table | Compare tree | built+tested mock/replay | n/a | `web/dash/v2.js`, `metatools.js` |
| 46 | Master feature table | Receipt viewer / per-run report | built; renders from fixtures | MEASURED / ESTIMATE / SIMULATED | `wattnot verify`; /v2.html#pProof |
| 47 | Master feature table | EU report card/link | built+tested (from receipt) | n/a | /eu.html; `wattnot report eu` |
| 48 | Master feature table | Voice guide UI | main: MOCK/replay; `feat/ask-live`: LIVE on the local server | MOCK / REPLAY / LIVE | Ask box mic |
| 49 | Master feature table | Hotspot overlay + hotspot-set | built+tested mock/replay | SIMULATED / MOCK | `web/dash/{hotspot,hotspotset}.js` |
| 50 | Master feature table | Wonderchart UI | built+tested mock/replay | ESTIMATE / SYNTHETIC | `web/dash/wonderchart.js` |
| 51 | Master feature table | Cost/budget panel | built+tested mock | ESTIMATE | /costbudget.html |
| 52 | Master feature table | Code-mode view | built+tested mock/replay | n/a | `web/dash/codemode.js` |
| 53 | Master feature table | Meta-tools card | built+tested mock/replay | n/a | `web/dash/metatools.js`, `live.js` |
| 54 | Master feature table | Five-layer teardown watchdog | built, dry-run tested; never ran a live delete | SIMULATED | `src/wattnot/longrun/watchdog/{core,local,agent,offbox}.py` |
| 55 | Master feature table | Heartbeat publisher | built, dry-run default | n/a | `src/wattnot/longrun/watchdog/publisher.py` |
| 56 | Master feature table | Spend guard (soft/hard $ caps) | built+tested mock/replay (dry-run) | ESTIMATE | /v2.html#pHome; /runs.html |
| 57 | Master feature table | Cloud timeout backstop | built+tested mock | ESTIMATE | `wattnot cloud` |
| 58 | Master feature table | Meta spend ledger | built+tested | MEASURED | `src/wattnot/meta/ledger.py` |
| 59 | Master feature table | Own rented GPU serving (dedicated-GPU rental) | ran live in the e2e test | MEASURED / ESTIMATE | `scripts/vm_docker.sh`, `scripts/run2_remote.py` |
| 60 | Master feature table | Hosted-inference provider adapter | built+tested live | MEASURED / ESTIMATE | `src/wattnot/receipt/cloud_adapter.py` |
| 61 | Master feature table | Serverless-GPU provider adapter (LIVE on `feat/mocks-live-1031` `373742c`: 1,397 J/req MEA | built untested (proof); GO-gated (sprint); probe DID NOT RUN | MEASURED / ESTIMATE / LIVE | `modal/proof_cpu30.py`, `modal/spark_topk_sprint.py` |
| 62 | Added in the Nov 1 audit (fe | Voice Ask, live | see notes | MEASURED / LIVE | Ask box (any page, bottom) |
| 63 | Added in the Nov 1 audit (fe | Public benchmark check | see notes | MEASURED / LIVE | see docs |
| 64 | Added in the Nov 1 audit (fe | Combined best-cap + prefix-cache block | see notes | MEASURED / LIVE | /v2.html#pHome; /runs.html |
| 65 | Added in the Nov 1 audit (fe | Every-cap sweep grid | see notes | MEASURED / LIVE | /v2.html#pHome; /runs.html |
| 66 | Added in the Nov 1 audit (fe | Dashboard login gate | see notes | n/a | /v2.html |
| 67 | Added in the Nov 1 audit (fe | Dashboard approvals and undo | see notes | MOCK | /v2.html |
| 68 | Added in the Nov 1 audit (fe | Approvals HTTP API | see notes | n/a | see docs |
| 69 | Added in the Nov 1 audit (fe | Receipts REST API | see notes | n/a | `wattnot verify`; /v2.html#pProof |
| 70 | Added in the Nov 1 audit (fe | Agent-waste proxy | see notes | n/a | see docs |
| 71 | Added in the Nov 1 audit (fe | Phase-aware clock sidecar | see notes | n/a | see docs |
| 72 | Added in the Nov 1 audit (fe | Search planner + ablation | see notes | ESTIMATE | see docs |
| 73 | Added in the Nov 1 audit (fe | Marginal-joule knee | see notes | n/a | see docs |
| 74 | Added in the Nov 1 audit (fe | Joule-SLO | see notes | n/a | see docs |
| 75 | Added in the Nov 1 audit (fe | Backend/effort/region router | see notes | MOCK | `wattnot route`; flow step multiprovider |
| 76 | Added in the Nov 1 audit (fe | Vision eye-test gate | see notes | SYNTHETIC / LIVE | see docs |
| 77 | Added in the Nov 1 audit (fe | Run event log | see notes | n/a | see docs |
| 78 | Added in the Nov 1 audit (fe | MoE top-k tuning (CPU) | see notes | ESTIMATE | see docs |
| 79 | Added in the Nov 1 audit (fe | Spark Open per-expert pinning | see notes | SIMULATED | /graph.html Spark trace panel |
| 80 | Added in the Nov 1 audit (fe | Run narrator | see notes | MEASURED / LIVE | see docs |
| 81 | Added in the Nov 1 audit (fe | Batch-size / KV-cache sweep | see notes | n/a | see docs |
| 82 | Added in the Nov 1 audit (fe | No-mock preflight | see notes | n/a | see docs |
| 83 | Added in the Nov 1 audit (fe | GPU VM controller | see notes | LIVE | see docs |
| 84 | Added in the Nov 1 audit (fe | Replay-only container + public site | see notes | MOCK | see docs |
| 85 | Added in the Nov 1 audit (fe | Data licences doc | see notes | n/a | see docs |
| 86 | Added in the Nov 1 audit (fe | Judges' hosted app | see notes | PLANNED | see docs |
| 87 | Update 2026-11-01 (overnight | Mocks removed from the live paths | done, merged | n/a | see docs |
| 88 | Update 2026-11-01 (overnight | Live Ask (answers from real receipts and runs) | done, live | n/a | Ask box (any page, bottom) |
| 89 | Update 2026-11-01 (overnight | LangChain proxy, real calls | done. First live run 7/7 (`dbc3a74`); fixes for reasoning bu | MEASURED / ESTIMATE | `python -m wattnot.flow` step langchain |
| 90 | Update 2026-11-01 (overnight | Rerun receipt `rcpt_20261031_rerun_cap325` | done. Signed offline with the test key, grades ESTIMATE, not | ESTIMATE | `wattnot verify`; /v2.html#pProof |
| 91 | Update 2026-11-01 (overnight | Data licences doc | done | n/a | see docs |
| 92 | Update 2026-11-01 (overnight | Mac dashboard + tunnel | works; the Pinggy tunnel is started by the user by hand, not | n/a | /v2.html |
| 93 | Update 2026-11-01 (overnight | Dataset picker (benchmarks, mixed, custom JSONL upload, "let WattNot decide"); receipt rec | done, merged | n/a | `wattnot verify`; /v2.html#pProof |
| 94 | Update 2026-11-01 (overnight | User-set p99 limit (default +10%), auto-approve cap drop option (58%); receipt `limits` bl | done, merged | n/a | `wattnot verify`; /v2.html#pProof |
| 95 | Update 2026-11-01 (overnight | Long-run plan (`--plan long`, ~4.6 h of blocks, 5 h booking, ESTIMATE) | plan only, not run | ESTIMATE | see docs |
| 96 | Update 2026-11-01 (overnight | Receipt converter to judge pack (`wattnot receipt convert`) | done, merged | n/a | `wattnot verify`; /v2.html#pProof |
| 97 | Update 2026-11-01 (overnight | GitHub watchdog vars reset (`WATCHDOG_ENABLED`, `WATCHDOG_LIVE` = false) | done | LIVE | see docs |
| 98 | Update 2026-11-01 (overnight | One-command install: `wattnot init` (watch-only by default), `wattnot preflight` (driver,  | done, merged; tested on a no-GPU box and with a mocked H100. | n/a | /v2.html#pHome; /runs.html |
| 99 | Update 2026-11-01 (overnight | Judges' hosted app and demo modes | PLANNED | PLANNED | see docs |
| 100 | Update 2026-11-01 (overnight | Rekor publish of receipts | PLANNED, after the long run | PLANNED | `wattnot verify`; /v2.html#pProof |

### Added since the inventory was written (Nov 1 afternoon, on main)

| # | Feature | Status | Label | Where to see it |
|---|---|---|---|---|
| 101 | SCI receipt block (energy, carbon, water) in every receipt; verifier recomputes it | merged `56306ed` | energy MEASURED, carbon and water ESTIMATE, embodied NOT_AVAILABLE | `wattnot verify`, `/eu.html`, EU report |
| 102 | Muse Spark 4-call trace: probe, proposal, gate, receipt | merged `56306ed` | LIVE with `--live-meta`, MOCK without | `/graph.html` Spark trace panel |
| 103 | "Meta models at work" strip, all 8 models (Spark, Wonder, Glimmer 30B, SAM 3.1, Voice Transcribe, Muse Code, Spark Open, Muse Image) | merged `a023a1a` | per call; Muse Image always SYNTHETIC; "no call in this run" for models that did not log | `/graph.html` strip |
| 104 | EU upload: drop a receipt + bundle, get the field report | merged (e2e3 fixes) | MEASURED only when the signature checks, else ESTIMATE | `/eu.html`, `POST /api/report/eu/upload` |
| 105 | Run matrix: 12 rounds (Baseten, Modal, H100, all), datasets, with/without WattNot | merged `193a1e4` | PLANNED until a round has real numbers | `/matrix.html` |
| 106 | Spark grades every answer, next to the answer checks (checks decide pass/fail) | merged `5784f30` | MOCK in dry runs | `/matrix.html` agreement column |
| 107 | Meta spend meter, $120 cap / $100 warning, split by model | merged `5784f30` | MEASURED from the spend log ($26.82, 5,307 calls on Nov 1) | `/matrix.html`, `GET /api/dash/meta/spend` |
| 108 | Credit meters: Baseten, Modal, E2E | merged `193a1e4` | ESTIMATE (Baseten balance is a $50 placeholder) | `/matrix.html` |
| 109 | Ask with glowing cards: empty Ask, cards glow, pick one, Spark explains and highlights | merged `193a1e4` | MOCK with `WATTNOT_META_MOCK=1` | Ask box at the bottom of every page |
| 110 | Voice Ask via Muse Voice Transcribe (speech to text only, no spoken reply) | merged | LIVE when mock is off | mic in the Ask box |

---

## 1. Setup on your Mac

Needs Python 3.11 or newer (`python3 --version`).

```bash
cd ~/Developer/wattnot
git checkout main
git pull --ff-only
git log --oneline -1            # expect 5784f30 or later

python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[sign,frameworks,dev,vision]'
```

Install all four extras. With only the base install, the flow fails two steps (`langchain` and `receipts-api`) because LangChain and `httpx` are missing. I hit exactly that on the box.

Environment for the review (safe, nothing paid):

```bash
export WATTNOT_META_MOCK=1      # every Meta call is MOCK, labelled MOCK on screen
export PYTHONPATH=.:src
```

To turn live Meta on later (paid, cents per call, only with your go):

```bash
unset WATTNOT_META_MOCK
export META_API_KEY=...         # from your password manager, never paste it in chat
# the flow also needs the flag:  python -m wattnot.flow --out /tmp/wn-flow --live-meta
```

---

## 2. Where to start: the dashboard

```bash
python -m wattnot.dash --port 8765
```

Open `http://127.0.0.1:8765/v2.html`. If 8765 is taken, use `--port 8766`. Stop it with Ctrl-C when done.

The top nav links every page. Go in this order:

1. **Landing** `/index.html`. Check: headline reads as a full framework, "Powered by Meta" chips (Muse Image is not in these chips yet, an open question for you).
2. **Dashboard** `/v2.html#pHome`. Check: headline KPI **−44.1% energy per correct answer** (MEASURED, 325 W cap + prefix caching, Oct 31 rerun), p99 **+9.7%** (MEASURED, inside the 10% limit), every number has a label badge.
3. **Configure** `/v2.html#pCfg`. Check: dataset picker, p99 limit, replay vs live-apply shown as replay.
4. **Run** `/v2.html#pRun`. Check: steps, labels, nothing claims live GPU.
5. **Compare** `/v2.html#pReport`. Check: stock vs tuned, 300 W shown as rejected (p99 +18%).
6. **Learn** `/v2.html#pLearn` and **Proof** `/v2.html#pProof`. Check: receipt id `rcpt_20261031_rerun_cap325`, grade ESTIMATE (offline test key, no Rekor yet). This receipt predates the SCI block.
7. **Runs** `/runs.html` and **Reports** `/report.html?run=rerun-1031-glimmer-325w`. Check: the run list opens, the Oct 31 Glimmer rerun report (325 W) loads.
8. **Live graph / architecture** `/graph.html`. Check: the layer diagram, the **Meta models at work** strip with all 8 models (Muse Image marked SYNTHETIC; models with no call say "no call in this run"), the **Spark 4-call trace** panel (probe → proposal → gate → receipt, MOCK in mock mode, with trace ids when live).
9. **Cost budget** `/costbudget.html`. Check: numbers labelled ESTIMATE.
10. **Run matrix** `/matrix.html`. Check: 12 rounds, each PLANNED; dataset per round; the with/without WattNot round; Spark grades next to the answer checks; Meta spend meter ($26.82 of $120, MEASURED); Baseten, Modal, E2E credit meters.
11. **EU Compliance** `/eu.html`. Check: the EU card with SCI fields (energy MEASURED, carbon 727 g/kWh ESTIMATE, water 0.36 L/kWh ESTIMATE); upload a receipt + its `.sigstore.json` (section 5) and see "Signature checked".
12. **Ask** (box at the bottom of every page). Check: press the → send button with the box empty, the cards glow; click one, Spark's explanation appears and that part of the page is highlighted. Typing works too. In mock mode the answer is MOCK. The mic uses Muse Voice Transcribe (text only, no voice reply).
13. **Meta tools preview** `/metatools.html`. Check: it opens and reads as a preview.

All 10 pages above returned HTTP 200 on the box.

---

## 3. The one-command flow (off GPU)

```bash
python -m wattnot.flow --out /tmp/wn-flow
```

About 35 seconds. The last line should be `[flow] 36 steps, 0 failed []`. Each step prints its label (MEASURED, REPLAY, MOCK). Output: `/tmp/wn-flow/flow-receipt.json` (signed), plus one folder per layer (`spark/`, `sam/`, `langchain/`, `eu/`, `route/`, ...). The run's event log is `/tmp/wn-flow/runs/run_*/events.jsonl`.

Other options: `--dataset own|gsm8k300|humaneval164`, `--live-meta` (paid), `--gpu-dir DIR` (pulled results of a real H100 pass).

---

## 4. Replay a recent run (the Oct 31 H100 rerun)

The rerun is receipt `receipts/rerun-1031/rcpt_20261031_rerun_cap325.json` with its signature bundle next to it, and the raw pulled data in `data/h100-rerun-1031/` (caps 300/325/350/375 W, baseline, prefix-cache and public-bench summaries). The numbers are MEASURED on the H100; anything you show from them now is a **replay** and is labelled REPLAY.

- **Dashboard:** the default dashboard already replays it (the −44.1% KPI is read from this receipt).
- **Joules from the receipt:**
  ```bash
  wattnot receipt joules receipts/rerun-1031/rcpt_20261031_rerun_cap325.json
  ```
  Box output: baseline 167.27 J per answer, 325 W 109.97 J per answer, −34.88% per good answer (MEASURED).
- **Flow:** `python -m wattnot.flow` uses this receipt for its GPU layers (they show `replay  REPLAY`).
- **Hosted judge-style replay** of a saved run, no GPU and no Meta calls at all:
  ```bash
  python -m wattnot.dash --port 8766 --replay-only --events /tmp/wn-flow/runs/<run_id>/events.jsonl
  ```
  `/api/dash/health` then reports `"mode":"replay","meta_calls":false`.

Did not work: `python -m wattnot.dash --results data/h100-rerun-1031` starts, but the GPU panel stays empty. `--results` expects the Oct 30 folder layout (`h100-*/sweep.csv`), not the rerun's `steps/capNNN.json`. Use the receipt route above for the rerun.

---

## 5. Verify a receipt, EU report, EU upload

```bash
wattnot verify receipts/rerun-1031/rcpt_20261031_rerun_cap325.json
```

Expected: `ok` for schema, sha256, signature (offline key), joules and labels; `none rekor` (not published yet, by design); grade **ESTIMATE**. Edit any number in a copy of the receipt and run it again: it must fail.

Note: this rerun receipt was signed on Oct 31, before the SCI block existed, so it has no SCI line. New receipts from the long run will carry it, and `verify` recomputes it.

EU report files (CSV + HTML):

```bash
wattnot report eu --receipts receipts/rerun-1031/rcpt_20261031_rerun_cap325.json --out /tmp/wn-eu/eu
open /tmp/wn-eu/eu.html
```

EU report over HTTP (receipts API):

```bash
WATTNOT_ROOT=$PWD uvicorn wattnot.api.app:app --port 8767
# then: http://127.0.0.1:8767/api/receipts/rcpt_20261031_rerun_cap325/report/eu   (add ?format=html)
```

EU upload: on `/eu.html` use the upload button with the receipt and its `.sigstore.json`. The API behind it is `POST /api/report/eu/upload` with JSON `{"receipt": ..., "bundle": ...}`. Sending the bare receipt file returns `{"code":"need_receipt"}`; that is expected.

Known issue: `wattnot verify /tmp/wn-flow/flow-receipt.json` fails today. The flow receipt is signed with a per-run key that is not in `verifier/trusted-keys.json`, and it uses labels like `MEASURED (no GPU here)` that the verifier rejects. The flow's own `flow-verify` step checks only its signature, so it passes. Not fixed yet.

---

## 6. Rollback

Each `pre-*` tag marks main **just before** the named change was merged. Today's tags, oldest first:

| Tag | Commit | Main just before |
|---|---|---|
| pre-nomock-1101 | b8428dd | mocks-to-live |
| pre-langchain-real-1101 | d93f327 | real LangChain test |
| pre-p99-setting-1101 | d5d3b76 | p99 setting |
| pre-longrun-plan-1101 | aae1e44 | long-run plan |
| pre-receipt-converter-1101 | 4fd99f8 | receipt converter |
| pre-dataset-picker-1101 | f7e4141 | dataset picker |
| pre-one-command-install-1101 | e63cae3 | one-command install |
| pre-e2e-fixes-1101 | 9813a30 | first E2E review fixes |
| pre-e2e2-fixes-1101 | aa8a6fb | second E2E review fixes |
| pre-eu-upload-1101 | c9e417c | EU upload |
| pre-sci-receipt-1101 | 6be0610 | SCI receipt + Spark trace |
| pre-muse-image-chip-1101 | 56306ed | Muse Image as eighth model |
| pre-e2e3-fixes-1101 | a023a1a | third E2E review fixes |
| pre-matrix-ask-1101 | 173d879 | run matrix + Ask cards |
| pre-prelaunch-1101 | 193a1e4 | Spark grading, spend KPI, dry run |
| pre-walkthrough-1101 | 5784f30 | this guide |

Look without changing anything:

```bash
git fetch --tags
git tag -l 'pre-*' --sort=creatordate
git log --oneline pre-prelaunch-1101..main      # what came after a tag
git diff --stat pre-prelaunch-1101 main
git switch --detach pre-prelaunch-1101           # try the old state; nothing moves
git switch main                                  # back
```

Roll back safely (new commits, history kept, no force push):

```bash
git switch -c rollback/<name> main
git revert -m 1 <merge-commit>                   # undo one merge, e.g. 5784f30
# or undo everything after a tag:
git revert -m 1 --no-edit $(git rev-list --merges --first-parent pre-prelaunch-1101..main)
# run tests: pytest -q ; then merge with --no-ff and push
```

Never `git reset --hard` on main and never `git push --force`.

---

## 7. Screenshot notes

Drop screenshots in `ui-review/` at the repo root. Name them `NN-page-issue.png` (for example `03-matrix-header-cramped.png`), and add one note per image in `ui-review/NOTES.md`. See `ui-review/README.md`. I will open the same page in my browser, fix it and send a before and after.

---

## 8. UI checklist

| # | Page / item | What to check | OK? |
|---|---|---|---|
| 1 | Landing `/index.html` | framework framing, Meta chips | [ ] |
| 2 | Nav bar | every link opens, active page shown | [ ] |
| 3 | Dashboard KPIs | −44.1% MEASURED, +9.7% p99 MEASURED, badges on every number | [ ] |
| 4 | Configure | dataset picker, p99 limit, replay by default | [ ] |
| 5 | Run | steps and labels, no false "live GPU" | [ ] |
| 6 | Compare | 300 W shown rejected | [ ] |
| 7 | Learn / Proof | receipt id, grade ESTIMATE, SCI block | [ ] |
| 8 | Runs list | opens, rows readable | [ ] |
| 9 | RUN2 report | loads (early 8B comparison run, not a Meta model, not a headline) | [ ] |
| 10 | Live graph | architecture renders, events animate | [ ] |
| 11 | Meta models strip | all 8 models, Muse Image SYNTHETIC, honest "no call" | [ ] |
| 12 | Spark 4-call trace | probe, proposal, gate, receipt in order, MOCK label in mock mode | [ ] |
| 13 | Cost budget | ESTIMATE labels | [ ] |
| 14 | Run matrix grid | 12 rounds, PLANNED, datasets, with/without round | [ ] |
| 15 | Spark grades | next to answer checks, agreement shown | [ ] |
| 16 | Meta spend meter | $26.82 / $120, per model split | [ ] |
| 17 | Credit meters | Baseten, Modal, E2E | [ ] |
| 18 | EU card | SCI fields with right labels | [ ] |
| 19 | EU upload | receipt + bundle shows "Signature checked"; bad file shows an error | [ ] |
| 20 | Ask glowing cards | empty Ask glows, pick one, explanation + highlight | [ ] |
| 21 | Ask typed / voice | typed answer; mic transcribes (text only) | [ ] |
| 22 | Meta tools preview | opens | [ ] |
| 23 | Mobile / narrow window | nothing overlaps | [ ] |
| 24 | Dark/light, contrast | text readable | [ ] |
| 25 | Flow run | 36 steps, 0 failed | [ ] |
| 26 | `wattnot verify` | rerun receipt grade ESTIMATE; edited copy fails | [ ] |
