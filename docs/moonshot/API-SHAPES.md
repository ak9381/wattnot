# JSON shapes for the frontend

Everything below is plain JSON that the CLI writes today. A frontend can read these files
(or a thin endpoint serving them) without any new backend work. Every object that holds
numbers has a `label`: MEASURED, ESTIMATE, SIMULATED or MOCK. Show it next to the number.

## Receipt extensions (`wattnot.receipt/1`)

The frozen receipt schema is unchanged. Extra top-level fields are part of the hash.

```jsonc
{
  "profile": "gpu" | "cloud" | "flex",      // optional; absent means a GPU run
  "grade": "VALID" | "ESTIMATE" | "SIMULATED" | "NOISE" | "UNVERIFIABLE",
  "joules": {
    "unit": "J", "label": "SIMULATED",
    "baseline":  { "j_per_answer": 27.27, "pass_rate": 0.82, "j_per_good_answer": 33.25 },
    "candidate": { "j_per_answer": 19.99, "pass_rate": 0.82, "j_per_good_answer": 24.38 },
    "change_pct": { "j_per_answer": -26.7, "j_per_good_answer": -26.7 }
    // cloud receipts also carry j_per_answer_range and j_per_good_answer_range {low, high}
  }
}
```

`j_per_good_answer` is null when the pass rate is 0. `wattnot verify <receipt>` and
`python verifier/wattnot_verify.py <receipt>` re-derive `joules` and fail the receipt if it
does not match; read the verifier's `--json` output for per-step results.

## Flex run (`wattnot flex run --json`)

An array, one entry per event:

```jsonc
[{
  "event": "demo-evt-1",
  "path": "flex-out/rcpt_flex_9201c41d840e.json",
  "grade": "SIMULATED",
  "sha256": "92f50e3c...",
  "outcome": {
    "status": "full" | "partial" | "none",
    "limit": null | "cap_floor" | "p99" | "quality",   // why it stopped short
    "target_pct": 25.0, "target_w": 205.14,
    "delivered_pct": 26.86, "delivered_w": 73.46,
    "seconds_to_target": 20.0,          // null if never reached
    "ramp_back_s": 4.0,                 // null if it did not recover in the window
    "settled_cap_w": 200,
    "p99_ms": { "before_mean": 1806.1, "event_mean": 2463.8, "event_max": 2725.0,
                "sla": 2709.2, "sla_violation_s": 1.0 },
    "pass_rate": { "before": 0.82, "event": 0.82 }
  }
}]
```

## Flex receipt, the `flex` block (inside `<id>.json`)

```jsonc
"flex": {
  "label": "SIMULATED",                      // GPU readings: SIMULATED model or MEASURED
  "event":  { "id": "demo-evt-1", "kind": "shed", "target_pct": 25.0,
              "duration_s": 300, "note": "..." },
  "signal": { "label": "SIMULATED", "source": "replayed demo events; ..." },
  "outcome": { /* same object as above */ },
  "baseline_power_w": 273.52,
  "controller": { "step_w": 10, "hold_s": 2.0, "sla_slack": 0.5, "...": "..." },
  "model": { "label": "SIMULATED", "note": "...", "idle_w": 70.0, "...": "..." },  // null on a GPU
  "cap_changes": [[30.0, 280], [32.0, 270]],  // [seconds, watts], first 200
  "cap_change_count": 34,                      // includes ramp-back moves
  "trace": { "csv_sha256": "...", "json_sha256": "...", "points": 420 },
  "scope": "GPU draw only. Cooling, CPUs, networking and the rest of the site are not shed.",
  "not_claimed": ["no site or grid level saving", "no carbon claim", "..."]
}
```

## Flex trace for the chart (`<id>.trace.json`)

```jsonc
{ "label": "SIMULATED", "dt_s": 1.0, "event": { "id": "...", "target_pct": 25.0, "duration_s": 300 },
  "points": [
    { "t_s": 0.0, "phase": "before" | "event" | "after",
      "power_w": 273.1, "cap_w": 700, "p99_ms": 1790.2, "pass_rate": 0.82,
      "throughput_rps": 10.0,
      "target_w": null,     // set from the event start
      "sla_ms": null }      // set from the event start
  ] }
```

Chart idea: power_w as a line with target_w as a dashed line, cap_w as a step line, p99_ms on a
second axis against sla_ms, the event window shaded. `wattnot flex show --json <receipt>`
gives `{label, grade, outcome, trace_ok, problems}` for a status badge.
