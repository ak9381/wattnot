# Canonical schemas (frozen Oct 27; change only through a task that bumps `schema_version`)

Two contracts. Everything else (CLI, API, frontend, verifier, EU export) reads these.

## 1. Run event (`run-event.schema.json`, v1)
One JSON object per line (JSONL on disk at `runs/<run_id>/events.jsonl`, the same objects over SSE at `GET /api/runs/{run_id}/events`). Envelope:

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `"wattnot.event/1"` | fixed |
| `run_id` | string | `run_YYYYMMDD_HHMMSS_<6hex>` |
| `seq` | int | strictly increasing per run, from 0; SSE `id:` = seq, so clients resume with `Last-Event-ID` |
| `ts` | string | RFC 3339 UTC |
| `type` | enum | see below |
| `step_id` | string \| null | canvas node id, e.g. `probe`, `baseline`, `plan`, `trial-07`, `gate-07`, `sign` |
| `parent_step_id` | string \| null | for edges on the canvas |
| `data` | object | per-type payload |

Event types and `data` payloads:
| `type` | `data` fields |
|---|---|
| `run.started` | `mode` (`container`\|`root`), `host` {provider, gpu, driver, cuda}, `model` (e.g. `meta-models/muse-spark-open-1026`), `workload` (`muse-code-task-set-v1`), `capabilities` (from probe) |
| `step.started` | `kind` (`probe`\|`baseline`\|`plan`\|`trial`\|`gate`\|`judge`\|`stats`\|`apply`\|`rollback`\|`sign`\|`publish`), `label`, `agent` (`wattnot`\|`muse-spark-1.3`\|`optuna`\|`muse-code`), `model` \| null, `effort` \| null (`low`\|`medium`\|`high`) |
| `step.progress` | `pct` 0–100, `message` |
| `step.finished` | `status` (`ok`\|`failed`\|`skipped`\|`blocked`), `duration_ms`, `tokens` {in, out} \| null, `meta_cost_usd` \| null, `retry` int |
| `telemetry.sample` | `gpu_index`, `power_w`, `energy_mj` (NVML counter), `sm_clock_mhz`, `mem_clock_mhz`, `temp_c`, `util_pct`, `source` (`nvml`\|`dcgm`\|`zeus`\|`estimate`). Sent at 1 Hz downsampled on SSE; 10 Hz goes to CSV only |
| `trial.proposed` | `trial_no`, `source` (`spark`\|`optuna`\|`grid`), `params` {max_num_seqs, enable_chunked_prefill, enable_prefix_caching, block_size, quantization (`none`\|`fp8`\|`awq`), gpu_mem_util, power_cap_w \| null, sm_clock_mhz \| null}, `reason` (≤ 200 chars) |
| `decision` | `choice` {`kind`: `cap`\|`knob`\|`stop`, `power_cap_w` \| null, `knob` \| null, `value` \| null}, `reason` (one line, ≤ 200 chars), `model` (`muse-spark-1.3` or `fallback`), `tokens` {`input`, `output`}, `clamped` (bool) |
| `trial.measured` | `trial_no`, `reps` (5), `wh_per_req` {mean, sd}, `usd_per_req` {mean, sd}, `p50_ms`, `p99_ms`, `ttft_ms`, `itl_ms`, `tokens_per_s`, `batch_fill`, `kv_use_pct`, `csv_path`, `label` (`MEASURED`\|`ESTIMATE`) |
| `gate.result` | `trial_no`, `check` (`fixed`\|`judge`\|`p99`\|`noise`), `passed` bool, `detail` {score, threshold, p_value, ci_low, ci_high} |
| `pareto.updated` | `points` [{trial_no, wh_per_req, p99_ms, quality, on_front}] |
| `action.applied` / `action.rolled_back` | `trial_no`, `params`, `reason` |
| `receipt.signed` | `receipt_id`, `sha256`, `grade`, `rekor_log_index` \| null, `path` |
| `run.finished` | `status` (`ok`\|`null_result`\|`failed`), `best_trial_no` \| null, `saving_pct` {mean, ci_low}, `billed` bool |
| `error` | `code`, `message`, `step_id` |

Rules: events are append-only; the run log on disk is the truth and SSE is a view of it; a frontend that only reads `events.jsonl` (replay mode) must render the same canvas as live.

## 2. Receipt (`receipt.schema.json`, v1)
One file per receipt: `receipts/<receipt_id>.json` + `.sigstore.json` bundle. Numbers, ids and settings only (Rekor is public and permanent). Fields:

- `spec`: `"wattnot.receipt/1"`; `receipt_id`; `created_at`; `run_id`
- `subject`: `model`, `host` {provider, gpu, driver}, `mode`, `workload` {name, version, n_requests, sha256 of the public replay set}
- `baseline` / `candidate`: `params`, `reps`, `wh_per_req` {mean, sd, samples[]}, `usd_per_req`, `p99_ms`, `quality` {fixed_pass_rate, judge_score}
- `result`: `saving_pct` {mean, ci_low, ci_high}, `welch` {t, df, p_value, alpha 0.05}, `billable` bool, `billed_saving_pct` (= ci_low if billable, else 0), `fee_share` 0.20
- `measurement`: `meter` (`nvml`\|`dcgm`\|`zeus`), `hz` 10, `csv` [{path, sha256}], `label` (`MEASURED`\|`ESTIMATE`), `idle_w` {before, after} \| null
- `attribution`: `method` `"token-share-capped (after Neuralwatt)"`, `cap`
- `planner_trace`: [{trial_no, source, params, reason}], `judge` {model `muse-spark-1.3`, verdict, agreement_on_30}
- `estimates`: `water_ml_per_req` {value, label `ESTIMATE`, method, source}, `sci_gco2e_per_req` {value, label, grid_factor_source}
- `grade`: `VALID` \| `ESTIMATE` \| `UNVERIFIABLE` \| `NOISE`
- `integrity`: `canonicalization` `"JCS RFC 8785"`, `sha256` of the canonical receipt minus `integrity`, `prev_sha256` (chain), `signer` (Sigstore identity), `rekor` {log_index, uuid, integrated_time} \| null

Grading: VALID = MEASURED + sha256 matches + signature OK + Rekor entry found. ESTIMATE = integrity OK but label ESTIMATE. NOISE = integrity OK but Welch p ≥ 0.05 (not billed). UNVERIFIABLE = no hash, no signature, or not our spec (e.g. Neuralwatt-style energy JSON).


## Additive change (Oct 26): baseline water + compare_to
- `estimates.baseline_water_ml_per_req` (optional): {value, label `ESTIMATE` (const), method, source}, mL per request, same shape as `estimates.water_ml_per_req`. If absent, UI derives the before water as ESTIMATE.
- `compare_to` (optional, string|null, `run_…` pattern): run_id of the baseline run. On receipts it is top-level `compare_to`; on events it goes in `run.started` `data.compare_to`. If absent, UI rebuilds the before canvas from `baseline`.
Both optional, so old receipts and events still validate.

## Additive change (Oct 26): run fetch API + UI view schema
- API (M09): `GET /api/runs/{run_id}` returns `{run_id, summary, events[], receipt|null}`; `GET /api/runs/{run_id}/events` is the SSE replay (Last-Event-ID resume). Used to load the `compare_to` baseline run.
- `ui-view.schema.json` (`wattnot.ui-view/1`): UI-only blocks derived from run events and receipts: `matrix_cards[]` (tier, model, wh_per_req, usd_per_req, p99_ms, quality as {before, after}; labels, run_id, compare_to, plus optional water/saving/grade) and `planner_reasons[]` (trial_id = trial_no, proposer = planner_trace source, reason, expected_effect, outcome). **Never signed, never part of the receipt sha256/JCS hash, not canonical**; the receipt and event log remain the truth.
