# Meta Tools v5: Supervisor, Code Application & Verification

Meta Tools v5 introduces three core capabilities to WattNot:
1. **Muse Wonder Supervisor**: Pareto-optimal power cap and clock frequency search that halts when the cost/quality frontier flattens.
2. **Muse Code Automation**: Unified diff patch generation, PR body authoring linked to signed receipts (`wattnot://<hash>`), and agent framework drop-in snippets.
3. **Receipt Quality Coverage**: quality judge verdict (hosted Muse Spark; Llama 3.1 8B fallback), agreement score, Wonder trace IDs, per-model spend, and patch hashes signed into the receipt body.

---

## 1. Spend Guard Updates

The persistent spend guard in `src/wattnot/meta/ledger.py` tracks worst-case reservations and actual settlement costs before network transmission:

- **Hard Cap**: Raised from $50.00 to $100.00 (`CAP_USD = 100.0`).
- **Soft Warning Limit**: Set to $80.00 (`SOFT_USD = 80.0`), issuing warnings when reservations enter the top 20% margin.
- **Model Pricing**: Added `muse-wonder` ($4.00/M in, $0.20/M cached in, $20.00/M out) alongside `muse-spark-1.3`.

Accounting labels apply to all recorded figures:
- Live API calls: `MEASURED`
- Unmetered hosted approximations: `ESTIMATE`
- Simulated surfaces & fixtures: `SIMULATED`
- Dry-run offline evaluations: `MOCK`

---

## 2. Muse Wonder Supervisor (`src/wattnot/meta/wonder.py`)

Muse Wonder orchestrates GPU power cap and clock tuning. It operates exclusively on compact per-trial JSON summaries — never raw telemetry samples:

```json
{
  "cap_w": 350,
  "clock_profile": "locked-high",
  "joules_per_correct": 14.8,
  "quality": 0.952,
  "p99_delta": 4.1,
  "usd_per_1k": 0.0072,
  "label": "MEASURED"
}
```

### Deterministic Local Pareto Stopping Rule
Wonder halts trial exploration when the $/quality Pareto front flattens:
- Calculates non-dominated frontier points minimizing `usd_per_1k` and maximizing `quality`.
- Evaluates relative frontier efficiency gain over the last $N$ trials (default $N=3$).
- If relative improvement $< \text{threshold}$ (default 1.00%), exploration halts deterministically with stop reason:
  `"$/quality Pareto front flattened: improvement < threshold over last N trials"`.
- This check is enforced locally in Python regardless of model suggestions.

### Mock and Live Modes
- **Mock Mode** (default, `WATTNOT_META_MOCK=1` or `--mock`): Deterministically tests bounded candidates `[(400, "base"), (350, "locked-high"), (300, "locked-mid"), ...]`.
- **Live Mode** (`--live`): Queries Muse Wonder via `GuardedClient` using strict JSON schema validation.

### CLI Subcommand
```bash
# Run supervisor in mock mode and write efficiency summary
python -m wattnot.cli meta wonder --input trials.json --out wonder_out/ --mock

# Inspect spend and remaining cap
python -m wattnot.cli meta spend
```

At conclusion, Wonder outputs:
- `efficiency_summary.json`: Complete trial ledger, non-dominated Pareto front, per-model spend breakdown, and trace IDs.
- `efficiency_summary.md`: Human-readable summary table with accounting tags on every metric.

---

## 3. Muse Code Integration (`src/wattnot/musecode/`)

### (a) Apply as PR (`apply_pr.py`)
Applies kept configuration changes (e.g. `power_cap_w`, `max_num_seqs`, `gpu_memory_utilization`) to target deployment repositories.

- **Verified CLI Invocation**:
  ```bash
  muse exec --json --workspace <repo> --worktree create --model <id> --prompt-file <file>
  ```
- **Mock / Dry-Run Mode** (default):
  Does not execute external processes. Performs deterministic Python updates on `launch.sh` and `values.yaml`, generates a unified diff patch (`patch.diff`), and writes `pr_body.md`.
- **Receipt Linking**:
  The PR body cites the verified receipt URI: `wattnot://<receipt_sha256>` and includes the patch SHA-256 hash.
- **`--open-pr` Safety Constraint**:
  Strictly refuses to create branches or execute `gh pr create` unless the git remote origin matches exactly `github.com/ak9381/wattnot-demo-deploy`.
- **Fixture Repository**:
  Provided under `examples/demo-deploy/` with baseline `launch.sh` and Helm `values.yaml`.

### (b) Framework Snippets (`snippets.py`)
Generates single-point `base_url` drop-in code for:
- `openai-python`
- `langchain`
- `llamaindex`

Validation harness:
- Spins up an ephemeral local mock upstream server on `127.0.0.1`.
- Executes snippet calls against the proxy.
- Missing dependencies are skipped gracefully without error.
- Results carry verified labels:
  - Successfully executed: `VALIDATED-LOCAL (mock upstream)`
  - Skipped due to uninstalled library: `MOCK (not run)`
- Outputs structured record in `snippets_validation.json`.

---

## 4. Signed Receipt Updates (`src/wattnot/receipt/build.py`)

All Meta Tools v5 verifications are placed directly inside the receipt's `measurement` object before canonicalization:

- **`judge_verdict`**: `"pass"` or `"fail"`
- **`judge_score`**: judge agreement fraction (e.g. `0.942` `[MEASURED]`)
- **`judge_model`**: the judge that made the verdict, e.g. `"meta-models/muse-spark-open"` (default) or `"meta-llama/Llama-3.1-8B-Instruct"` (fallback); `judge_path` says which
- **`spark_trace_id`**: Deterministic decision hash (e.g. `spark-9a8b7c6d5e4f3210`)
- **`wonder_trace_ids`**: List of supervisor trace identifiers
- **`per_model_spend`**: Spend tally for `spark`, `wonder`, `glimmer`, `llama`, and `sam`
- **`musecode_patch_sha256`**: Canonical hash of generated deployment patch

Because these fields reside in the receipt body alongside measurements, they are hashed by JCS (RFC 8785) into `integrity.sha256` and verified by Ed25519 offline signatures. Tampering with any judge or trace field causes signature verification to fail.

---

## 5. SAM 3.1: Hotspot Sets the Cap (`src/wattnot/sam/hotspot_cap.py`)

Identifies thermal or rack hotspots in server photos via SAM 3.1 segmentation. When a hotspot is located on a specific GPU:
- The planner lowers the power cap on that GPU only (by a bounded step, e.g. 50W, never below `min_cap_w` of 250W).
- All other GPUs in the rack remain strictly unchanged at their existing power caps.
- The signed receipt records:
  - Mask: `bbox` `[x1, y1, x2, y2]`, polygon coordinates, compact RLE, and deterministic SHA-256 hash.
  - Decision: `gpu_id`, `old_cap_w`, `new_cap_w`, `delta_w`, `min_cap_w`.
  - Reason: Technical justification under 200 characters to pass receipt linting.
- UI Component (`web/dash/hotspot.js`, `web/dash/hotspot.css`):
  - Renders a "Cooling Hotspots" glass card with interactive SVG rack overlay highlighting the hot GPU slot and displaying before/after caps.
  - Exported as `mountHotspotCard(el, receipt)` and `renderHotspotHTML(data)`.

---

## 6. Voice Guide (`src/wattnot/meta/voiceguide.py`)

A hands-free multimodal assistant for querying runs and navigating dashboards:
- **Audio Pipeline**: Audio input -> Muse Voice Transcribe (deterministic mock default; real only with `--live`) -> text -> Spark intent classification.
- **Three Read-Only Intents**:
  1. `navigate`:
     - `"compare runs"` -> returns route to Compare view (`/dash/comparetree.html`) with prompt to pick runs.
     - `"show last night's report"` -> resolves latest run by timestamp and navigates to its report route.
  2. `answer`:
     - Read-only Q&A grounded exclusively in verified signed receipts (hash check passed) and EU efficiency reports.
     - Cites `receipt_id` or `report_id` on every answer.
     - Refuses unanswerable questions outside verified data.
  3. `configure`:
     - Returns a pre-fill configuration object (`workload`, `stack`, `caps_w`).
     - Explicitly requires the user to review parameters and press Start.
- **Strict Execution Boundary**:
  - The voice guide must **NEVER** launch, stop, or mutate runs.
  - Enforced by an immutable action allowlist: `ALLOWED_ACTIONS = {"navigate", "answer", "prefill", "explain"}`. Attempting any other action raises `VoiceGuideActionError`.
  - Text-only answers (no synthetic TTS).
- **Intents**:
  1. `navigate`: routes to Compare or latest run report.
  2. `answer`: read-only Q&A grounded exclusively in verified signed receipts and EU efficiency reports.
  3. `configure`: returns a pre-fill object for run setup (requires user review and pressing Start).
  4. `explain`: point-and-explain on-screen metrics and elements (e.g. "what does 25% mean?", "what does 350 W mean?", "explain power cap").
- **Point-and-Explain Runtime Tagging (`web/dash/explainmap.js`)**:
  - Automatically queries selectors across all pages at runtime and tags elements with `data-explain` attributes (`data-explain-id`, `data-explain-value`, `data-explain-label`, `data-explain-source`, `data-explain-summary`), preserving any pre-existing `data-explain` attributes.
  - Fast local JS matcher scoring numbers, units, and keywords against on-screen values, titles, and labels.
  - Lavender palette glowing pulse animation (respects `@media (prefers-reduced-motion: reduce)` with solid border).
  - Apple glassmorphism popover positioned next to the element (strictly clamped to viewport bounds) providing a 2-3 sentence technical explanation, source receipt/run ID, and verified label (`MEASURED`, `ESTIMATE`, `SIMULATED`, `MOCK`, `REPLAY`, `ADVICE`).
  - Ambiguity handling: when multiple elements closely match (e.g. identical percentage values), all candidates are highlighted with clickable choices asking *"Which one?"*.
- **Selector Mapping by Page**:
  | Page | Target Components | Exact Selectors | Extracted Fields |
  |---|---|---|---|
  | All-runs dashboard (`runs.html`) | Bento KPI cards, run rows, trend & donut charts | `.kpis .kpi`, `#runsTable tbody tr`, `.charts .card` | Title (`.name`), Value (`.big`), Label (`.v-lab`), Source (`rcpt_llama_350w`) |
  | Run report (`report.html`) | Stepped flow cards, meta line, side panel details | `.flow .card`, `.step .card`, `.meta-line`, `aside.side .kv` | Step Name (`.k`), Value (`.val`), Explanation (`.sub`), Label (`.v-chip`) |
  | Compare & Setup (`v2.html`) | Compare branch cards, placement rows, gates, setup inputs | `.cmp.glass`, `.cmp-cards .cmp`, `.wr-t tbody tr`, `.wr-gates li`, `.cmp-m li`, `.ov-kpi.glass`, `#pCfg .card, [data-step]` | Lever Name (`.cmp-t`), Saving (`.cmp-v`), Gate Status, Input Config |
  | Live watch-run (`web/run`) | Live counters, efficiency tiles, waterfall bars, graph nodes | `#savings .cnt`, `#savings .eff`, `#savings .wf li`, `#nodes .node` | Counter Name (`.ch span`), Value (`b.mono`), Node Title (`.nt`), Delta |
  | Meta Tools preview (`metatools.html`) | Demo cards, supervisor stats, hotspot card, EU directive | `.demo-explain-card`, `#metatools-mount .kpi-box`, `#hotspot-mount .hotspot-stat-box`, `#eu-directive-card .eulink-metric-box` | Metric Title, Value, Label (`MOCK`/`MEASURED`), Summary |
- **Playwright Verification (`scripts/shots_explain.py`)**:
  - Serves `web/` locally, intercepts API routes, injects `voiceguide.js` and `explainmap.js`, types queries, and generates screenshots in `/workspace/hackathon/meta5-shots/`:
    - `voice-explain-metatools.png` (Demo cards with 25% popover)
    - `voice-explain-runs.png` (Runs dashboard KPI highlight)
    - `voice-explain-report.png` (Report step card highlight)
    - `voice-explain-v2.png` (Compare branch highlight)
    - `voice-explain-run.png` (Live watch-run counter highlight)
    - `voice-explain.png` (Primary overview screenshot)
- **API Endpoint**:
  - `POST /api/voiceguide` and `GET /api/voiceguide?q=...` supporting query text and audio payloads.
- **UI Component (`web/dash/voiceguide.js`, `web/dash/voiceguide.css`, `web/dash/explainmap.js`)**:
  - Self-mounts into `<header>` or creates a fixed top-right bar with mic button (`MediaRecorder`) and text input.
  - Immediate state transition: "Listening…" on mic press, "Thinking…" on submission (zero silent lag).
  - Rapid local path provides initial intent and ack in `<300 ms` followed by cited answer.
  - Labeled `MOCK` during mock execution.
- **Script Tag Deployment**:
  Per instructions, existing HTML files were not modified during this phase. To enable the voice guide across other pages, add the script tags:
  ```html
  <link rel="stylesheet" href="./voiceguide.css">
  <script type="module" src="./voiceguide.js"></script>
  <script type="module" src="./explainmap.js"></script>
  ```
  Target pages for script deployment:
  - `web/dash/report.html`
  - `web/dash/runs.html`
  - `web/dash/index.html`
  - `web/dash/comparetree.html`
  - `web/dash/v2.html`
  - `web/run/index.html`

---

## 7. EU Efficiency Report (`src/wattnot/report/eu.py`)

Extends Delegated Regulation (EU) 2024/1364 data-center reporting with one-click export from signed receipts:
- **Core Metrics**:
  - `it_energy_measured_window`: Total IT energy in kWh over measured request window (`MEASURED` or `SIMULATED`).
  - `joules_per_correct_answer`: Energy efficiency normalized by task quality / pass rate in `J/correct`.
  - `water_per_request`: Direct and indirect water consumption estimate in `mL/request` (`ESTIMATE`).
  - `energy_saving_billed`: Verified lower bound saving percentage with Welch significance testing.
- **Regulatory Framing**:
  - Explicitly framed as "supports EU Energy Efficiency Directive data-center reporting".
  - Marked with "CSRD roadmap" alignment.
  - Prominent disclaimer: "Not a compliance claim; facility-level metrics require operator metering."
- **API & Helper**:
  - `generate_report(receipt, format="html"|"csv"|"json")`: Generates the complete report from a verified receipt in one click.
  - Endpoint `GET /api/receipts/{receipt_id}/report/eu?format=html|csv|json`.
- **UI Component (`web/dash/eulink.js`, `web/dash/eulink.css`)**:
  - Renders a directive summary card with quick metrics and one-click download buttons for HTML, CSV, and JSON.
  - Exported as `mountEuLink(el, receipt)` and `renderEuLinkHTML(data)`.

---

## 8. Dashboard Preview (`web/dash/metatools.html`)

A consolidated preview page (`web/dash/metatools.html`) brings together all Meta Tools v5 components:
- Muse Wonder Supervisor trial overview & Pareto stop details
- Muse Code PR diff preview & framework snippets
- Llama 8B Quality Gate verdict & agreement score
- SAM 3.1 Cooling Hotspots rack map and bounded cap derate
- EU Energy Efficiency Directive export link
- Self-mounted Voice Guide in top-right slot


## Built partly with Muse Code
Two scoped commits on this branch were written headless with Muse Code 1.4.14 (`muse exec --json --workspace <repo> --prompt-file <task>`), model muse-spark-1.3-contributor:
- `ab8d2a1` Apply-as-PR live command builder + JSONL trace parsing. Session 01a18439-b5c5-7ca0-9443-ca696a2b1547, 21 calls, ~$0.010 (MEASURED from the run.usage event).
- `c85f679` voice guide run-setup prefill parser + intent tests. Session 01a1843b-abbb-7120-97bc-9f68374323cc, 39 calls, ~$0.015 (MEASURED from the run.usage event).
Both are logged in the Meta spend ledger.

## EU efficiency report sample
`reports/eu/eu_report_rcpt_20261027_run2_cap350.{html,csv}` from the signed RUN2 350 W receipt. Supports EU Energy Efficiency Directive data-center reporting fields; not a compliance claim; CSRD is roadmap.
