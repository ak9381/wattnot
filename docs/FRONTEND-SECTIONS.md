# Frontend sections for the UI UX Designer bot

Base: locked DESIGN.md. All data comes from `schemas/run-event.schema.json` and `schemas/receipt.schema.json` (see `schemas/SCHEMAS.md`). Every number shows a MEASURED/ESTIMATE label. Fixture data first; live SSE later.

Judging weights: Impact 30%, Meta tooling 25%, Technical depth 20%, Demo 25%. The first screen should show who benefits and the saving (Wh and $ per request). No chat-UI look anywhere: no chat bubbles or prompt box as the main view.

## S1. Run canvas (n8n-style, React Flow)
- **Source:** `events.jsonl` or SSE `/api/runs/{id}/events`.
- **Nodes:** one per `step_id`, edges from `parent_step_id`. Node kinds: probe, baseline, plan, trial, gate, judge, stats, apply, rollback, sign, publish.
- **Node fields:** `label`, `agent` (wattnot / muse-spark-1.3 / optuna / muse-code), `model`, `effort`, `status` (running / ok / failed / skipped / blocked), `duration_ms`, `retry`, `tokens.in/out`, `meta_cost_usd`, `step.progress.pct` and `message`.
- **Trial nodes** add `params`, `source` (spark / optuna / grid), `reason`, `wh_per_req` mean±sd, `p99_ms`, `ttft_ms`, `tokens_per_s`, `label`.
- **Gate nodes** add `check` (fixed / judge / p99 / noise), `passed`, `detail.score/threshold/p_value/ci_low/ci_high`.
- **Header:** `mode` (container / root), `host.provider/gpu`, `model`, `workload`, run `status`, `saving_pct.mean/ci_low`, `billed`.
- **Live strip:** `telemetry.sample` 1 Hz sparkline of `power_w`, `sm_clock_mhz`, `temp_c`, plus `action.applied` / `action.rolled_back` markers.

## S2. Receipt + Verifier
- **Receipt card:** `subject.model/host/mode/workload`, baseline vs candidate `params`, `wh_per_req`, `usd_per_req`, `p99_ms`, `quality`, `result.saving_pct` (mean, ci_low, ci_high), `welch.p_value`, `billable`, `billed_saving_pct`, `fee_share`, `measurement.meter/hz/label/csv links`, `idle_w`, `estimates.water_ml_per_req` (always ESTIMATE), `sci_gco2e_per_req`, `planner_trace`, `grade`, `integrity.sha256/signer/rekor.log_index`.
- **Verifier:** drop zone; step list with schema, sha256 (expected vs got), signature (signer), rekor (log index link), label, then the final grade pill: VALID / ESTIMATE / NOISE / UNVERIFIABLE / FAIL at step.
- **Side by side:** a Neuralwatt-style JSON marked UNVERIFIABLE next to ours marked VALID.
- **Samples:** 3 real and 3 forged.

## S3. Pareto + Planner
- **Scatter:** from `pareto.updated.points[]`. Each point has `trial_no`, `wh_per_req`, `p99_ms`, `quality` and `on_front`, with 5-rep error bars from `trial.measured` sd. The baseline is marked.
- **Point detail:** `params`, gate results, Spark `reason`, CSV link.
- **Ablation:** three arms (cold, warm, grid), each with `trials_to_best`, `seconds_to_best` and `wh_to_best`.
- **Optional:** a p99 SLA slider that moves the picked point.
- **Null-result state:** "No win beyond noise" with the data still shown.

## S4. Rack / Floor map
- **Source:** `rack/masks.json`, with `source` (sam3.1 / manual), `objects[].id/kind/polygon/gpu_indices`, `iou_vs_manual` and `status` (ok / cut).
- **Overlay:** per-GPU `power_w` and current cap from `telemetry.sample` and `action.applied`.
- **Fallback:** the banner "MANUAL FALLBACK (SAM attempt in repo)" shows when the source is manual.

## Cut
No landing page or settings screens. The EU export is a link from S2, not its own screen.


## Schema additions (Oct 26)
- Receipt card / run-compare: before water reads `estimates.baseline_water_ml_per_req` (always ESTIMATE); fall back to deriving it if absent.
- Run-compare: if receipt `compare_to` or `run.started` `data.compare_to` is set, load that run's events for the real before canvas; else rebuild from receipt `baseline`.

## Additive change (Oct 26)
- Baseline run for compare: `GET /api/runs/{run_id}` (events + receipt) or SSE replay `GET /api/runs/{run_id}/events`.
- Matrix cards and planner reasons use `schemas/ui-view.schema.json` (derived, unsigned, not in the receipt hash).
