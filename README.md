# WattNot

## Submission overview

- **Track:** Indie (solo, Akash K Raju). **Problem statement:** Push the Frontier (energy).
- **Built during the hackathon:** all code here was written Oct 26 to Nov 4, 2026. Before Oct 26
  there were only planning docs. Commit history shows the rest.
- **Meta models at runtime:** Muse Spark 1.3 (planner and judge), Muse Wonder (trial planner and
  chart reader), Muse Voice Transcribe (spoken Ask), SAM 3.1 (rack hotspots), and the open-weights
  models we serve and tune: **Muse Glimmer 30B** on an H100 and **Muse Spark Open** on Baseten. Both
  are load-bearing: they are the models whose watts we measure and cut. Llama 3.1 8B is a comparison model.
- **Bonus categories we are aiming for:** Best Build on Meta Open Weight Models, Best Agentic Use-Case.
- **Try it without any keys:** see [Keyless replay](#keyless-replay). Every number is labelled
  MEASURED, ESTIMATE, MOCK or REPLAY.

## Meta models at work

Each layer of WattNot is driven by a Meta model. In a live run every call below is a real API
or GPU call with a trace id, latency and $ in the run's event log, except Muse Code (replayed in
demos) and Muse Image (a pre-generated SYNTHETIC set, never a live call); the dashboard's
**Meta models at work** strip and **Muse Spark trace** panel read that log. Anything replayed
from an earlier run is labelled REPLAY; nothing is shown that did not happen.

| Layer | Meta model | What it does in the run |
|---|---|---|
| Operator | Muse Spark (`muse-spark-1.3`) | four traced calls: probe (reads GPU telemetry + preflight), proposal (cap or serving change), gate (judges the quality verdict; the fixed rule still decides), receipt (explains the signed receipt) |
| Planner | Muse Wonder (`muse-wonder-1.0`) | plans the next trial and reads the Pareto (Wonder) chart |
| Served model | Muse Glimmer 30B | the model being tuned on the H100 under each power cap |
| Facility | SAM 3.1 | rack hotspot audit on the room scan |
| Voice | Muse Voice (`muse-voice-transcribe-1.0`) | spoken Ask and tune |
| Change | Muse Code | the change as a sandboxed PR (replay in demos; no PR is opened) |
| Routing | Muse Spark Open on Baseten | multi-provider routing pass |
| Test data | Muse Image | made the test images for the vision quality gate (any miss rejects a change) and the SAM hotspot test set. Muse Image is blocked in India, so these are pre-generated sets labelled SYNTHETIC, never a live call |

Full list with costs: [Meta models used](#meta-models-used).

WattNot is an agent that cuts the energy and cost of each LLM inference request without making the model worse. It tries one serving change at a time (GPU power cap, batching, KV-cache settings, FP8, prefix caching) and keeps it only if three fixed checks pass: a quality gate on a fixed prompt set (accuracy may drop by at most 1 point by default), a p99 latency budget, and a Welch noise test so a saving has to beat run-to-run jitter. The model can explain a verdict but never overrides it; the rule decides. Each kept change becomes a hash-chained receipt signed offline (Ed25519) with before and after Wh and $ per request, and `wattnot verify` re-checks it: change one number and verification fails. It is built on Meta models: Muse Spark picks each step, and the default served model is Meta's open-weights Muse Glimmer 30B (`meta-models/Muse-Glimmer-30B`, Apache 2.0) on vLLM, with Llama 3.1 8B Instruct (`meta-llama/Llama-3.1-8B-Instruct`) as a comparison model. We also tune Muse Spark Open (NVFP4) on a large multi-GPU box, because it needs 700 GB+ of VRAM, more than one H100.

Token trimmers cut your bill, but not your watts. WattNot measures the joules each request burns on the GPU, and checks quality before it keeps any change.

H100 run: `bash scripts/h100_run.sh --dry-run` first, then see [docs/runbooks/H100.md](docs/runbooks/H100.md). Adaptive top-k ([docs/ADAPTIVE-TOPK.md](docs/ADAPTIVE-TOPK.md)) is not in the H100 run; it is planned on Muse Spark Open via Modal. Earlier top-k tests used non-Meta models and are kept only as dated history.

**Every model WattNot runs is a Meta model (Glimmer, Llama, Spark, Spark Open, Wonder, SAM 3.1, Voice, Code, plus Muse Image for pre-generated SYNTHETIC test sets). See [Meta models used](#meta-models-used).**

## Run it in 2 minutes

You need Python 3.11, 3.12 or 3.13 and git. No GPU. A Meta Model API key is optional: with one you run WattNot live, without one you get the recorded real runs.

```bash
git clone https://github.com/ak9381/wattnot.git && cd wattnot
./run-local.sh smoke      # about a minute: doctor, a simulated run, the dashboard up and down
./run-local.sh            # with a key in .env: live Meta API run. Without one: replay of our real runs
```

On Windows, run the same commands in WSL or Git Bash.

### Your settings

Copy the example file and fill in what you have. `.env` is gitignored, and WattNot never prints its values.

```bash
cp .env.example .env            # macOS / Linux / WSL
```

```powershell
Copy-Item .env.example .env     # Windows PowerShell
```

### The modes

| Command | What runs | Needs | Numbers |
|---|---|---|---|
| `./run-local.sh doctor` | Checks Python, disk, GPU, driver, NVML, power-limit permission, vLLM and your key. Each line says PASS, WARN or FAIL, plus the fix. Changes nothing. | nothing | - |
| `./run-local.sh gpu` (optional) | The full framework on your GPU. Stock vs a power cap on 4 prompt tiers, with the quality gate (pass rate drop of 2 points or less, p99 within +10%). Then the token savers if a key is set. | NVIDIA GPU you control, root, a vLLM server, `WATTNOT_VLLM_URL` | energy MEASURED (NVML); water and $ ESTIMATE |
| `./run-local.sh savers` | Cheapest-first routing with step-up and the thinking budget, then the agentwaste proxy and context saver on agent tasks, all on live Meta models. Spend is capped by `WATTNOT_MAX_SPEND` (default $0.50). | `META_API_KEY` | tokens, latency, cost MEASURED; energy ESTIMATE |
| `./run-local.sh sim` | The same harness with a fake GPU and an oracle model | nothing | all MOCK |
| `./run-local.sh replay [name]` | Dashboard on one of our recorded runs: `h100-max-1103` (default), `modal-r1`, `modal-r2`, `baseten-r1`, `baseten-r2` | nothing | MEASURED replay |

`make judge` and `make doctor` do the same as the first two commands. Results go to `out/judge-<time>/`.

### GPU prerequisites

- NVIDIA driver installed. `nvidia-smi` must work.
- Root, or sudo, to change power limits. Run `sudo -E ./run-local.sh gpu`.
- vLLM serving Muse Glimmer 30B (Meta open weights) on the same box: `vllm serve meta-models/Muse-Glimmer-30B --port 8000 --reasoning-parser muse_glimmer` (or `bash scripts/glimmer_serve.sh`, which we used). Keep `WATTNOT_MODEL_CONFIG=muse-glimmer-30b`. Only Meta models count for this hackathon, so the judge path never calls anything else.
- The stock power limit goes back on at exit, on Ctrl-C and on error, both from the harness and from the launcher. If anything kills the process hard, run `sudo nvidia-smi -i 0 -pl <stock W>` (doctor shows the stock range).

When doctor finds no GPU, an untested GPU or no permission, the power-cap features turn off with a message saying why. The token savers still run.

### Meta models used, and where each runs

| Model | Where it runs | What it does in WattNot |
|---|---|---|
| Muse Glimmer 30B (open weights) | vLLM on a rented NVIDIA H100 80GB, BF16. FP8 was also tried on Modal A100-80GB: it kept accuracy on eye and hearing but was rejected on the full round (accuracy -4.16 pts, MEASURED), so the H100 stays BF16 | The model being served. Every power cap, thinking budget, saver and agentwaste arm is measured on its answers |
| Muse Spark (Meta Model API) | Hosted | Cheapest-first effort routing with step-up, answer grading for the quality gate, and the judge `savers` mode |
| Muse Spark Open (open weights) | Baseten, hosted | The hosted-API round: effort routing and the LangChain saver |
| Muse Wonder (Meta Model API) | Hosted | Plans which cap and clock to try next |
| SAM 3.1 (Meta Model API) | Hosted | Rack photo to a map of the servers a run touched, used as a cross-check |
| Muse Code | Hosted | Opened the 375 W cap PR from a run result |

Provenance for the H100 run: the full event log, per-block receipts and signed sweep receipts are in [`replays/h100-max-1103/`](replays/h100-max-1103/manifest.json) (`./run-local.sh replay` plays it). Modal and Baseten runs are in [`replays/INDEX.md`](replays/INDEX.md). Run history with commits and costs: [`docs/RUN_HISTORY.md`](docs/RUN_HISTORY.md).

### Tested on

| Where | What we ran | Power cap |
|---|---|---|
| NVIDIA H100 80GB (bare cloud VM with root access) | The full stack: power caps through NVML, vLLM serving, quality gate, signed receipts, LangChain, agentwaste, eye and spoken arms, sealed GSM8K holdout | yes |
| Modal (hosted A100-80GB) | Glimmer 30B served on Modal, with LangChain and context saver runs | no, the platform owns the GPU |
| Baseten (hosted Muse Spark Open API) | Effort routing, with the LangChain saver | no, we don't control the GPU |

The power-cap features need root and NVML on a GPU you control. The token savers work on any OpenAI-compatible endpoint. Any other GPU is untested: doctor flags it, and caps stay off unless you set `WATTNOT_ALLOW_UNTESTED_GPU=1`.

### Troubleshooting

- **`setup failed`**: run `bash scripts/setup.sh` to see the error. Most often Python is older than 3.11.
- **`HTTP 401` / `403`**: the key is wrong or expired. Check `META_API_KEY` in `.env`, with no quotes and no spaces.
- **`HTTP 429`**: WattNot retries 3 times (2, 5 and 10 s). If it still fails, wait a minute or lower `WATTNOT_PER_TIER`.
- **`request failed: TimeoutError`**: no network, or `META_BASE_URL` is wrong. Timeouts are not retried, because the call may already have been billed.
- **doctor says `vllm FAIL`**: the server isn't up yet, or the URL is wrong. `curl http://127.0.0.1:8000/v1/models` (your host and port) should list a model.
- **`power-limit permission FAIL`**: run with `sudo -E`. On serverless GPUs this can't work, so use `savers`.
- **Port busy**: a free port is picked and printed. Set `WATTNOT_PORT` to choose one.
- **Ctrl-C**: safe at any point. The power limit is restored and nothing is left running.

## For judges

**Fastest path — one command, then `wattnot demo`.** Clone the repo and run the
setup script for your OS; it makes a local virtual environment, installs WattNot
into it, and leaves you one step from the dashboard. No GPU, no network beyond
PyPI, no key, nothing billed. Typical time from clone to dashboard is under two
minutes.

macOS (Intel or Apple Silicon) and Linux:

```bash
git clone https://github.com/ak9381/wattnot.git && cd wattnot
bash scripts/setup.sh && source .venv/bin/activate && wattnot demo
```

Windows (PowerShell):

```powershell
git clone https://github.com/ak9381/wattnot.git; cd wattnot
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
.\.venv\Scripts\Activate.ps1; wattnot demo
```

Prefer to do it by hand? With Python 3.11+ on your PATH: `pip install -e .` then
`wattnot demo`. The demo picks a free port if 8765 is busy and opens your
browser; pass `--no-browser` or `--port N` if you'd rather it didn't. If
anything goes sideways, see [Troubleshooting](#troubleshooting).

**What you'll see.** `wattnot demo` replays a recorded H100 run: the measured
power-cap sweep (RUN2), each Muse Spark planner decision with its reasoning,
the quality and p99 gate (including why 325 W was rolled back), and a signed
receipt you can re-verify. Start on the **Proof** tab.

**Nothing needs a key, a GPU or a paid call.** The demo plays back recorded
logs — no Baseten key, no Meta API key, no Modal account, no GPU, no money
spent. (A hosted replay of the same data is also at
https://ak9381.github.io/wattnot/ if you'd rather not install anything.)

**Every number carries a label:** **MEASURED** (recorded on a real H100),
**ESTIMATE** (scaled from a measured number), and **SIMULATED / MOCK**
(a software model or placeholder, hidden until you flip the "Illustrations"
switch). The headline below is MEASURED.

- **Headline (RUN2, MEASURED):** -27.1% joules per correct answer at a 350 W cap on an H100 (17.03 to 12.42 J), pass rate unchanged at 93.7% on 666 checked answers. RUN2 quality was judged by fixed answer checks (deterministic string and number matching, no model as a judge); results with the Meta judge (hosted Muse Spark, with a Llama 3.1 8B fallback) are reported separately when measured.
- **What Spark decided:** every Muse Spark planner call in RUN2 (400 W, 350 W, 325 W, stop), with its reasoning, latency, tokens and the gate verdict. 325 W failed the p99 gate (+10.95% against a 10% limit); the (i) button explains why.
- **Verify a receipt yourself:**

  ```bash
  pip install -e .
  wattnot verify --chain verifier/samples/chain/      # hash, signature, labels, hash chain
  wattnot verify --rekor-online receipt.json          # also look the entry up in the public Rekor log
  wattnot verify replays/baseten-r1/receipt.json      # a round receipt: signed with that run's own key, so
                                                      # it shows 'warn' on the signer and grades ESTIMATE
  python verifier/wattnot_verify.py --chain verifier/samples/chain/   # standalone, stdlib only
  ```

  The sample receipts are signed offline, so the Rekor step reports "not logged" until the owner publishes them.
- **Leaderboard (net joules per correct answer):** on the Proof tab of the replay, rebuilt by `scripts/reproduce_leaderboard.sh`. Method and how to submit: [docs/joules-per-correct-answer.md](docs/joules-per-correct-answer.md).
- **Context saver:** prefix caching A/B, memory compression and tool-output dedup, each behind the quality gate: [docs/CONTEXT-SAVER.md](docs/CONTEXT-SAVER.md).
- **Drop-in for agent frameworks:** set the base URL in openai-python, LangChain or LlamaIndex: [docs/frameworks.md](docs/frameworks.md) (tests are MOCK).
- **The whole journey on one page:** innovations, features, measured results, decisions and tests as a mind map and knowledge graph — [docs/JOURNEY.md](docs/JOURNEY.md).

| Setup | J / correct (mean ± sd) | vs default | p99 | Gate |
|---|---|---|---|---|
| Default vLLM (no cap) | 17.03 ± 0.14 (MEASURED) | +0.0% | +0.0% | baseline |
| WattNot power cap 500 W | 16.38 ± 0.01 (MEASURED) | -3.8% | +0.7% | passed |
| WattNot power cap 450 W | 14.69 ± 0.81 (MEASURED) | -13.7% | +1.8% | passed |
| WattNot power cap 400 W | 13.81 ± 0.01 (MEASURED) | -18.9% | +4.7% | passed |
| WattNot power cap 350 W | 12.42 ± 0.00 (MEASURED) | -27.1% | +7.7% | passed |
| WattNot power cap 325 W | 11.84 ± 0.01 (MEASURED) | -30.5% | +10.9% | failed: p99 up 10.9% (limit 10%) |

Hosted rounds (2 Nov, our set plus public GSM8K + HumanEval, stock vs WattNot on the same items;
replays in [replays/INDEX.md](replays/INDEX.md)):

| Round | J / correct, stock -> WattNot | Accuracy, stock -> WattNot | Verdict | Cost |
|---|---|---|---|---|
| Modal A100, Glimmer 30B, r04c (modal-r1) | 2254.7 -> 821.6 J, -63.6% (MEASURED, NVML) | 95.8% -> 99.0% (MEASURED) | approved (2 blocks failed, replaced by later reruns) | $4.87 (MEASURED) |
| Modal A100, Glimmer 30B, r04f | -67.6% (MEASURED) | held | rejected: p99 88.4 -> 104.6 s (MEASURED), a safety catch | $4.42 (MEASURED) |
| Modal A100, Glimmer 30B, r05 (modal-r2) | 2954.6 -> 678.4 J, -77.0% (MEASURED, NVML) | 96% -> 97% (MEASURED) | see replay | ~$3.47 (ESTIMATE) |
| Baseten, Muse Spark Open, r02 public (baseten-r1) | 719.3 -> 482.5 J (ESTIMATE: hosted API, no GPU meter) | 98% -> 98% (MEASURED) | see replay | $0.54 Baseten + $0.21 Meta (MEASURED) |
| Baseten, Muse Spark Open, r03 own set (baseten-r2) | 2087.3 -> 685.0 J (ESTIMATE) | 79.4% -> 90.2% (MEASURED) | see replay | $0.88 Baseten + $0.16 Meta (MEASURED) |

- **Signed receipts on the replay:** https://ak9381.github.io/wattnot/receipts/ . They are signed offline with the repo's public test key, so grades stop at ESTIMATE. The RUN2 receipt carries the Spark trace ID and joules per model.
- **If the live demo's server times out,** the dashboard switches to the published replay and says so.

### Meta models used

Every Meta model WattNot uses, and what we built on top of each:

| Model | What we built on it | Measured cost |
|---|---|---|
| Muse Spark (`muse-spark-1.3`, hosted) | the planner that picks the next power cap and explains gate failures; judge; agent-waste and coding-set runs; the voice guide's intent step; narrator notes | $23.08 over 3459 calls by 2026-10-28 (MEASURED, API ledger); $30.46 in total by 2 Nov 9:32 PM IST (MEASURED, API ledger, all Meta models) |
| Muse Wonder (`muse-wonder-1.0`, hosted) | the sweep supervisor (`--planner wonder`) and the Wonder chart that reads the sweep and plots the knee | from the API ledger (MEASURED when run) |
| SAM 3.1 (`sam-3.1`, hosted) | hotspot finder: segments rack photos into units (kept off the main path) | $0.12 over 47 calls (MEASURED, API ledger) |
| Muse Voice (`muse-voice-transcribe-1.0`, hosted) | Ask by voice: speech to text for the voice guide, which answers from signed receipts only | from the API ledger (MEASURED when run) |
| Muse Glimmer 30B (`meta-models/Muse-Glimmer-30B`, open weights) | the served model we tune on the H100 (power cap, thinking budget, tiers, LangChain agent, agent-waste saver) | own H100, cost is GPU hours |
| Llama 3.1 8B Instruct (open weights) | the served model in RUN2; small model in small-first; judge fallback | own H100, cost is GPU hours |
| Muse Spark Open (Baseten, Modal) | big model when the small one fails a check; hosted planner; the multi-provider routing arm | $0.004, 1 call (MEASURED) |
| Muse Image (`muse-image-1.0`) | the pre-generated test images for the vision quality gate and the SAM hotspot test set, labelled SYNTHETIC; blocked in India, so no live call in any run | none in our runs |

- **Endpoint health:** `wattnot health` probes each hosted endpoint and writes a FALLBACK ledger (`data/judge/health-*.json`).
- **Labels:** every number is MEASURED, ESTIMATE, SIMULATED, MOCK, REPLAY or ADVICE. SIMULATED and MOCK content is hidden by default; the "Illustrations" switch shows it.
- **Not measured yet (long GPU run):** small-model-first escalation rate and net savings, the hosted Muse Spark judge (Llama 3.1 8B fallback), Muse Spark Open with fewer experts, and the token trimmer and caps on one workload (the stacked figure is an ESTIMATE until then).

## Quickstart

### One command, watch-only

```bash
pip install wattnot && wattnot init
```

Until the package is on PyPI, install straight from this repo:
`pip install "git+https://github.com/ak9381/wattnot" && wattnot init`.

`wattnot init` runs a read-only preflight (NVIDIA driver, CUDA version, power-cap permission,
vLLM version), writes `~/.wattnot/config.json`, and starts in **watch-only** mode: WattNot reads
telemetry and shows what it would save, with a receipt, and changes nothing. Layers this machine
can't use (for example power caps on a serverless GPU, or with no GPU at all) are switched off
with the reason, never faked. When you trust what you see:

```bash
wattnot preflight          # re-check the machine any time (--json for scripts)
wattnot init --act         # allow WattNot to apply approved changes
```

### Meta API key (for live Muse Spark, Wonder and Voice Transcribe)

Watch-only mode, the tests and replays need no key. For live Meta model calls, give WattNot
your Meta Model API key one of two ways (it is only held in memory and never written to a receipt):

```bash
export META_API_KEY=...    # in your shell; never commit it
# or log in once with the Muse CLI, which stores it in ~/.config/muse/auth.json
muse login
```

To exercise the Meta paths without a key or any spend, set `WATTNOT_META_MOCK=1`; every answer
is then labelled MOCK. The dashboard in replay mode (`python -m wattnot.dash --replay-only`) always uses the mock.

### Keyless replay

No GPU, no keys, no spend. This replays a real recorded round (labels kept, plus REPLAY):

```bash
pip install -e .
PYTHONPATH=src WATTNOT_META_MOCK=1 python -m wattnot.dash --replay-only \
  --events replays/modal-r2/events.jsonl --port 8795
# open http://127.0.0.1:8795/index.html
```

All four replays are listed in [replays/INDEX.md](replays/INDEX.md). Modal round 1 (`modal-r1`) is
built on the approved r04c round. The later round r04f is kept in
[data/archive/INDEX.md](data/archive/INDEX.md) as a safety catch: it cut joules per correct answer
by 67.6% (MEASURED), but p99 latency rose from 88.4 s to 104.6 s (MEASURED), so WattNot refused it.

### Run it live on your own account

Judges use their own keys. Keys stay in the shell that starts WattNot (or `~/.config/wattnot/env`
on your machine); the dashboard shows key names and present/missing, never values, and never
sends them anywhere except the provider's own API. Every path has a free `--dry-run` (MOCK
numbers, $0). Live runs cost real money on your account: check the estimate first.

| Platform | Minimum env | Preflight | Dry run ($0) | Live command | Cost (ESTIMATE) |
|---|---|---|---|---|---|
| Own GPU (H100/A100 with vLLM) | `META_API_KEY`, `WATTNOT_BASE_URL` (your vLLM), `HF_TOKEN` for gated weights | `wattnot preflight` | `python scripts/e2e/round.py --provider h100 --dry-run` | `wattnot init --act`, then the sweep in [docs/runbooks](docs/runbooks) | your GPU hours |
| Modal | `META_API_KEY`, Modal token (`modal token new`, stored in `~/.modal.toml`) | `python scripts/e2e/preflight.py --provider modal` | `python scripts/e2e/round.py --provider modal --dry-run` | `WATTNOT_MODAL_GO=1 python scripts/e2e/modal_stage.py --rounds r05 --sets gsm8k humaneval` | ~$4.6 per public round, hard stop $20 per round |
| Baseten | `META_API_KEY`, `BASETEN_METRICS_KEY` (a Baseten workspace key with your own Muse Spark Open deployment) | `python scripts/e2e/preflight.py --provider baseten` | `python scripts/e2e/baseten_round.py --round r02 --mode smoke --limit 2 --dry-run` | `python scripts/e2e/baseten_stage.py --rounds r02 --pr-round none --sets gsm8k humaneval` (`--pr-round none` = no pull request) | ~$1 Baseten + ~$0.6 Meta per public round, hard stop $20 |
| Docker (own GPU) | same as own GPU, passed with `-e` | `docker run --gpus all wattnot-agent preflight --json` | n/a | see [Docker](#docker) | your GPU hours |

From the dashboard: start it (`wattnot demo --no-browser`, or `python -m wattnot.dash`), open the
**Configure** page, pick the platform (Baseten or Modal A100) and datasets (`ours`, `gsm8k`,
`humaneval`), then walk the steps. The last steps run the free pre-launch checks (keys, model,
caps, git) and show the cost estimate; a live start needs every required check to pass and the
cost to be confirmed. Mock mode spends nothing. The H100/own-GPU path is not wired to the
dashboard yet: start it from the command line above. Caps: $20 per round (Modal and Baseten),
$90 total for a Modal stage, Meta $120 hard / $100 warning.

### Environment variables

Every variable the code reads is listed, names only, in [`.env.example`](.env.example). Copy it to
`.env` (gitignored), fill in only what you need, and load it with `set -a; . ./.env; set +a`.

### Docker

```bash
docker build -f Dockerfile.agent -t wattnot-agent .
docker run --gpus all -p 8765:8765 wattnot-agent            # preflight, then the dashboard, watch-only
docker run --gpus all wattnot-agent preflight --json        # any wattnot command
```

The host needs only an NVIDIA driver and the NVIDIA container toolkit. Without `--gpus` (or
with no GPU) the image still runs and preflight reports no GPU.

### Developer checks

```bash
python -m pytest -q                          # unit tests, no GPU or network
python -m wattnot.proof.run report --out results/proof   # rebuild a report, no GPU needed
```

On an H100 host with vLLM up:

```bash
python -m wattnot.proof.run control --out results/proof  # gate check first
python -m wattnot.proof.run step --cap 500 --out results/proof
python -m wattnot.proof.run full --out results/proof     # control, planner caps, grid, report
```

`full` runs the whole proof. `step` and `control` write one result each, so a planner on another machine can drive the host. The dry run on a fake meter lives in the tests: `python -m pytest tests/test_proof.py -q`.

## Troubleshooting

The demo is meant to run on whatever a judge already has — Windows, macOS
(Intel or Apple Silicon) or Linux, with or without a GPU or Docker.

- **"Python 3.11 or newer" error, or `wattnot: command not found`.** WattNot
  needs Python 3.11+. Check with `python --version` (`python3 --version` on
  macOS/Linux). Install from [python.org/downloads](https://www.python.org/downloads/)
  — on Windows tick "Add python.exe to PATH" — then open a new terminal and run
  the setup script again. The `wattnot` command only exists after you activate
  the virtual environment (`source .venv/bin/activate`, or
  `.\.venv\Scripts\Activate.ps1` on Windows); until then use
  `python -m wattnot.cli demo`.
- **PowerShell won't run the script ("running scripts is disabled").** Start it
  with `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1`, which
  bypasses the policy for that one run without changing a system setting.
- **`python -m venv` fails on Linux.** Some distributions ship venv separately:
  `sudo apt install python3-venv` (Debian/Ubuntu), then re-run `bash scripts/setup.sh`.
- **The browser didn't open, or the port is busy.** The demo prints the URL it
  is serving (it picks a free port if 8765 is taken). Open that URL by hand, or
  re-run with `wattnot demo --port 8080`. Use `--no-browser` on a headless box.
- **No GPU? No Docker?** Neither is needed. The demo replays recorded logs; any
  `nvidia-smi`/NVML telemetry falls back to a labelled mock, and nothing is
  launched.
- **"pip install failed" behind a proxy or offline.** The only network the
  setup touches is PyPI. On a restricted network, set `PIP_INDEX_URL` to your
  mirror, or install the wheels once on a connected machine and copy the
  `.venv`.
- **Nothing should ever bill you.** The demo makes no Meta/Baseten/Modal call;
  no key is read. If a command asks for a key, you're past the demo — those
  live paths are opt-in and documented in `docs/runbooks/`.

## Cloud mode (hosted Muse Spark Open on Baseten)

No GPU box? On a hosted model you can't touch the batch size or the power cap, but you can change how hard the model thinks. `wattnot cloud tune` runs your prompts at `xhigh` (the default) as the reference, then tries `low`, `medium` and `high` per prompt class. It keeps the cheapest level whose answers still pass the same checks and whose p99 latency holds, counting any retry at `xhigh` when a cheap answer fails. The receipt then shows, per class, tokens, USD and latency (MEASURED), what the same requests would have cost at `xhigh`, and an energy range (ESTIMATE). `wattnot cloud audit` checks the receipt's token totals against Baseten's usage meter, and `cache-ab` / `probe` test prompt layout and cache lifetime.

```bash
wattnot cloud demo --out cloud-demo        # offline replay of a made-up fixture, every number SIMULATED
export BASETEN_METRICS_KEY=...                    # env only, never a flag or a file
wattnot cloud tune --live --budget-usd 10 --record data/cloud/recorded-run1.json
```

What is and isn't checked yet is listed in [docs/runbooks/BASETEN.md](docs/runbooks/BASETEN.md). The bundled numbers are SIMULATED until a live run is recorded.

## Meta open-weight models

- **Muse Spark Open on Baseten picks the power caps.** In the cap sweep (`--planner baseten-open`), each step sends Muse Spark Open (`meta-models/muse-spark-open`, served by Baseten) the caps still allowed and the measured results so far, and it answers with the next cap to try or "stop". Its pick only counts if it is on the allow-list; the gate still decides keep or roll back.
- **Every call goes in the receipt.** `planner_trace[].call` records the provider (`baseten`), the model id, a SHA-256 of the exact prompt, the latency in ms (MEASURED) and whether it fell back.
- **Safe fallback.** If the call fails or the answer is off the list, a deterministic bisect rule picks the cap instead. That step is logged as `FALLBACK` and marked `source: fallback` in the receipt, so it never passes as a model pick.
- One real test call (`scripts/spark_open_planner_smoke.py`, made-up SIMULATED history shaped like RUN2): Spark Open picked 350 W after 400 W passed and 325 W failed on p99, in 4.95 s, 575 in / 766 out tokens, about $0.004 (latency and tokens MEASURED, dollars ESTIMATE from list price). 350 W is the cap RUN2 kept on the H100. Record: [docs/runs/spark-open-planner-smoke.json](docs/runs/spark-open-planner-smoke.json).
- **Modal works the same way.** `--planner modal-open` runs the same planner on Modal's Shared Endpoint for Muse Spark Open, at the same list price. Every compare (`python -m wattnot.spark_open.h2h`) lists three placements on the same prompts: our GPU, Baseten and Modal. The router picks one per workload on quality and p99 and writes the reasons into the receipt. Setup: [docs/runbooks/MODAL.md](docs/runbooks/MODAL.md).
- The served models we tune are Meta open weights too: Muse Glimmer 30B and Llama 3.1 8B Instruct on vLLM.

## Meta API usage (capped at $120)

Muse Spark makes WattNot's agent decisions: the next knob to try, the order of reasoning-effort levels, the judge verdicts and plain-words receipt notes. Every call goes through a spend guard that keeps a ledger (`~/.wattnot/meta_spend.json`), refuses a call that would pass **$120**, and warns at $100 (Meta credits: $150). `wattnot meta spend` shows the total, a per-purpose breakdown and what is left; `wattnot meta check` confirms the model answers. Details, the price used and the recorded session: [docs/runbooks/META-USAGE.md](docs/runbooks/META-USAGE.md).

**Spend snapshot (MEASURED, 2026-10-28; newer: the Meta ledger reads $30.46 MEASURED on 2 Nov, and each hosted round's cost is in its receipt, see [replays/INDEX.md](replays/INDEX.md)).** The Meta API ledger totals **$23.20** (3,506 billed calls: $23.08 for the Muse Spark planner, $0.12 for SAM), well under the cap (now **$120**). Hosted endpoints are billed separately and tracked in their own small ledgers: the Baseten head-to-head (one planner call plus the compare) is about **$0.005**, and the hosted A/B (Baseten + Modal arms of Muse Spark Open) totals **$0.14**. All three are MEASURED from billed token counts at list price.

## Flex mode (grid events, SIMULATED until the H100 run)

`wattnot flex run` replays a grid event ("cut GPU draw by 25% for 5 minutes") through a shed
controller and writes a signed receipt: how much it cut, how fast, whether p99 and quality
held, and how long ramp-back took. Today it runs on a software power model, so every number is
SIMULATED; see [docs/FLEX.md](docs/FLEX.md).

## Approve before apply

WattNot does not change a power cap, a vLLM setting, a routing policy or a flex shed by
itself. It files a proposal: what changes, the saving with its label, the quality and p99
checks, and a receipt preview. A person runs `wattnot proposals approve <id>`, and only then
`apply`. Every step goes in a hash-chained audit trail linked to the signed receipt. There is
a local JSON API for a frontend, an optional auto-approve policy (off by default), expiry and
rollback. See [docs/APPROVALS.md](docs/APPROVALS.md). The unattended H100 script takes an
explicit `--pre-approved` flag.

## Router (replay on recorded runs, region SIMULATED)

`wattnot route replay` takes a recorded cloud run and picks the effort per request class that
answers correctly for the fewest joules, then shows what always-`xhigh` would have cost next to
it. A failed quality gate always rules a candidate out. Region is a what-if on rounded grid
numbers, labelled SIMULATED. See [docs/ROUTE.md](docs/ROUTE.md).

## Mock certificates (always MOCK)

`wattnot certificate mint` turns one verified receipt into a one-page statement with the
receipt hash, a QR code and a permanent MOCK / NOT TRADABLE / NOT A CARBON CREDIT watermark.
NOISE and unverifiable receipts are refused, and a receipt can be certified once. It is a
mock for a roadmap slide, not a product. See [docs/CERTIFICATE.md](docs/CERTIFICATE.md).

## Public benchmark check

`scripts/public_bench_ab.py` repeats the stock vs best-cap comparison on small slices of two
public sets, so the saving is not only measured on our own prompts. Same server, same prompts,
same seed; only the power cap changes. Rounds alternate, energy comes from the GPU's own NVML
counter (MEASURED), and the result is the paired change in energy per correct answer with its
95% interval and p-value.

| Set | Slice | Grader | Source (pinned) | Licence |
|---|---|---|---|---|
| GSM8K test | first 60 items | final number vs the `####` gold answer | [openai/grade-school-math](https://github.com/openai/grade-school-math) @ `3101c7d` | [MIT](data/public_bench/LICENSE.gsm8k) |
| HumanEval | first 40 items | the item's own tests, run in a sandboxed subprocess | [openai/human-eval](https://github.com/openai/human-eval) @ `b9453e8` | [MIT](data/public_bench/LICENSE.humaneval) |

The slices live in `data/public_bench/` with their licences, and the script checks their sha256
before it runs.

## Status

Built during the Meta Global AI Developer Hackathon, Oct 26 – Nov 4 2026. Before Oct 26 we had only planning docs, no code.

## Docs

- `docs/JOURNEY.md`: the build journey as a mind map and knowledge graph — innovations, features, measured results, decisions and tests
- `docs/PRD-00-overview.md`: goals, scope and rules
- `docs/modules/`: one PRD and one TDD per module (M01–M13)
- `docs/schemas/`: run-event, receipt and ui-view schemas
- `docs/FRONTEND-SECTIONS.md`: the four app screens
- `docs/TASKS.md`: ordered build list
- `docs/runbooks/BASETEN.md`: cloud mode on Baseten (effort router, receipt, meter audit, cache)

## Credits

Per-request energy is split by token share with a per-request cap, following the method Neuralwatt describes. See `src/wattnot/meter/attribution.py`.

The default rack image in `rack/ai/` is an AI-generated illustration, not a photo of a real rack. The real Cisco photo in `rack/cisco/` is kept as a fallback.

## License

MIT, see `LICENSE`.

## Fleet impact (estimate)

Scale a measured Wh-per-request saving from a receipt to a monthly figure:

```
wattnot impact --wh-delta 0.01 --requests-per-gpu-per-day 100000 --gpus 8
```

Defaults: tariff 0.12 USD/kWh (an assumption), PUE 1.0, 30 days. All are flags.
Water: 0.36 L/kWh (on-site cooling, ESTIMATE); up to 1.8 L/kWh including power-plant water (ESTIMATE).
Every output line is labelled ESTIMATE; only the Wh delta is measured.
