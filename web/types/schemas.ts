// Generated from schemas/*.json by scripts/gen_ts_types.py.
// Do not edit by hand; run the generator after a schema change.

// ---- event (wattnot.event/1) ----

export interface RunEvent {
  schema_version: "wattnot.event/1";
  run_id: string;
  seq: number;
  ts: string;
  type: "run.started" | "step.started" | "step.progress" | "step.finished" | "telemetry.sample" | "trial.proposed" | "decision" | "trial.measured" | "gate.result" | "pareto.updated" | "action.applied" | "action.rolled_back" | "receipt.signed" | "recommendations" | "run.finished" | "error";
  step_id?: string | null;
  parent_step_id?: string | null;
  data: Record<string, unknown>;
  compare_to?: string | null;
}

// ---- receipt (wattnot.receipt/1) ----

export interface Receipt {
  spec: "wattnot.receipt/1";
  receipt_id: string;
  created_at: string;
  run_id: string;
  subject: Record<string, unknown>;
  baseline: {
    params: {
      max_num_seqs?: unknown;
      enable_chunked_prefill?: unknown;
      enable_prefix_caching?: unknown;
      block_size?: unknown;
      quantization?: unknown;
      gpu_mem_util?: unknown;
      power_cap_w?: unknown;
      sm_clock_mhz?: unknown;
    };
    reps: number;
    wh_per_req: {
      mean: number;
      sd: number;
      samples?: number[];
    };
    usd_per_req?: {
      mean: number;
      sd: number;
      samples?: number[];
    };
    p99_ms: number;
    quality: {
      fixed_pass_rate?: number;
      judge_score?: number;
    };
  };
  candidate: {
    params: {
      max_num_seqs?: unknown;
      enable_chunked_prefill?: unknown;
      enable_prefix_caching?: unknown;
      block_size?: unknown;
      quantization?: unknown;
      gpu_mem_util?: unknown;
      power_cap_w?: unknown;
      sm_clock_mhz?: unknown;
    };
    reps: number;
    wh_per_req: {
      mean: number;
      sd: number;
      samples?: number[];
    };
    usd_per_req?: {
      mean: number;
      sd: number;
      samples?: number[];
    };
    p99_ms: number;
    quality: {
      fixed_pass_rate?: number;
      judge_score?: number;
    };
  };
  result: Record<string, unknown>;
  measurement: {
    label: "MEASURED" | "ESTIMATE" | "SIMULATED";
  };
  attribution?: Record<string, unknown>;
  planner_trace: unknown[];
  estimates?: {
    water_ml_per_req?: {
      value: number;
      label: "ESTIMATE";
      method?: string;
      source?: string;
    };
    baseline_water_ml_per_req?: {
      value: number;
      label: "ESTIMATE";
      method?: string;
      source?: string;
    };
    sci_gco2e_per_req?: Record<string, unknown>;
  };
  grade: "VALID" | "ESTIMATE" | "SIMULATED" | "UNVERIFIABLE" | "NOISE";
  integrity: {
    canonicalization: "JCS RFC 8785";
    sha256: string;
    prev_sha256?: string | null;
    signer?: string | null;
    rekor?: Record<string, unknown> | null;
  };
  compare_to?: string | null;
}

// ---- ui-view (https://wattnot.dev/schemas/ui-view.schema.json) ----

export type RunId = string;

export type LabelledNumber = {
  value: number | null;
  label: "MEASURED" | "ESTIMATE" | "SIMULATED";
  derived?: boolean;
} | null;

export interface BeforeAfter {
  before?: number | null;
  after?: number | null;
}

export interface MatrixCard {
  tier: string;
  desc?: string;
  model: string;
  wh_per_req: BeforeAfter;
  usd_per_req: BeforeAfter;
  p99_ms: BeforeAfter;
  quality: BeforeAfter;
  water_ml_per_req?: {
    before?: LabelledNumber;
    after?: LabelledNumber;
  };
  saving_pct?: {
    mean?: number;
    ci_low?: number;
    ci_high?: number;
  } | null;
  billable?: boolean;
  grade?: "VALID" | "ESTIMATE" | "SIMULATED" | "UNVERIFIABLE" | "NOISE";
  labels: ("MEASURED" | "ESTIMATE" | "NOISE" | "BLOCKED" | "NULL_RESULT" | "UNVERIFIABLE")[];
  receipt_id?: string | null;
  run_id: RunId;
  compare_to?: RunId | null;
}

export interface PlannerReason {
  trial_id: number;
  run_id?: RunId;
  proposer: "spark" | "optuna" | "grid" | "baseline" | "human";
  model?: string | null;
  params?: Record<string, unknown>;
  reason: string;
  expected_effect?: {
    wh_per_req_delta_pct?: number;
    p99_ms_delta_pct?: number;
    quality_delta?: number;
    note?: string;
  } | null;
  outcome: "pending" | "on_front" | "dominated" | "failed_quality" | "noise" | "rolled_back" | "best";
  measured?: {
    wh_per_req?: number;
    p99_ms?: number;
    quality?: number;
  } | null;
}

export interface UiView {
  spec: "wattnot.ui-view/1";
  derived: {
    from: ("run_events" | "receipts")[];
    signed: false;
    in_receipt_hash: false;
    generated_at?: string;
    source_run_ids?: RunId[];
  };
  matrix_cards: MatrixCard[];
  planner_reasons: PlannerReason[];
}
