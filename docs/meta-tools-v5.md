# Meta Tools v5: Supervisor, Code Application & Verification

Meta Tools v5 introduces three core capabilities to WattNot:
1. **Muse Wonder Supervisor**: Pareto-optimal power cap and clock frequency search that halts when the cost/quality frontier flattens.
2. **Muse Code Automation**: Unified diff patch generation, PR body authoring linked to signed receipts (`wattnot://<hash>`), and agent framework drop-in snippets.
3. **Receipt Quality Coverage**: Llama 8B judge verdict, agreement score, Wonder trace IDs, per-model spend, and patch hashes signed into the receipt body.

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
- **`judge_score`**: Llama 8B agreement fraction (e.g. `0.942` `[MEASURED]`)
- **`judge_model`**: `"meta-llama/Llama-3.1-8B-Instruct"`
- **`spark_trace_id`**: Deterministic decision hash (e.g. `spark-9a8b7c6d5e4f3210`)
- **`wonder_trace_ids`**: List of supervisor trace identifiers
- **`per_model_spend`**: Spend tally for `spark`, `wonder`, `glimmer`, `llama`, and `sam`
- **`musecode_patch_sha256`**: Canonical hash of generated deployment patch

Because these fields reside in the receipt body alongside measurements, they are hashed by JCS (RFC 8785) into `integrity.sha256` and verified by Ed25519 offline signatures. Tampering with any judge or trace field causes signature verification to fail.

---

## 5. UI Glass Card Component

The dashboard UI components reside in `web/dash/`:
- `metatools.js`: Modular rendering logic exporting `mountMetaTools(el, receipt)` and `compareRows(receipts)`.
- `metatools.css`: Glassmorphic styling utilizing the lavender palette (`#DCD6E4` down to `#171724`) on `#080B10` black background.
- `metatools.html`: Standalone preview page rendering `sample-meta5-receipt.json` (`MOCK`).
- `metatools.test.mjs`: Node test suite verifying HTML generation and comparative row extraction.

Visual indicators:
- Pass Pill: Muted sage (`#8EA898`, `✓ PASS`)
- Fail Pill: Muted rose (`#C27A88`, `✗ FAIL`)
- Numbers: Monospaced Fira font with adjacent accounting badge.
