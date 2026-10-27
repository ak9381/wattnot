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

## Router replay (`wattnot route replay --json`, or the `--out` file)

```jsonc
{
  "format": "wattnot.route-replay/1",
  "label": "SIMULATED",                       // ESTIMATE only if the source run was live
  "labels": { "usd": "SIMULATED", "joules": "ESTIMATE", "gco2": "SIMULATED", "region": "SIMULATED" },
  "source_receipt": { "receipt_id": "cloud_...", "sha256": "...", "mode": "replay-synthetic",
                      "label": "SIMULATED" },
  "backend": "baseten/muse-spark-open-1026",
  "home_region": "us-east", "regions": ["ap-south", "..."], "tie_pct": 3.0, "requests": 30,
  "grid": { "label": "SIMULATED", "source": "static fixture (...)", "as_of": "...",
            "kind": "average, not marginal" },
  "classes": [{
    "class": "arith", "requests": 6,
    "baseline":          { /* row */ },       // always xhigh in the home region
    "effort_only":       { /* row */, "reason": "medium in us-east: passes the gate, ..." },
    "effort_and_region": { /* row */, "reason": "...", "tie_broken_by_grid": true },
    "excluded": [{ "id": "arith:low@us-east", "status": "excluded", "why": "failed: fixed" }]
  }],
  "totals": {
    "baseline":          { "usd": 0.3096, "wh_low": 3.14, "wh_high": 31.45, "j_mid": 62270.2, "gco2_mid": 6.57 },
    "effort_only":       { /* same keys */ },
    "effort_and_region": { /* same keys */ },
    "change_pct": { "effort_only": { "usd": -55.84, "j_mid": -54.33, "gco2_mid": -54.33 },
                    "effort_and_region": { "usd": -55.84, "j_mid": -54.33, "gco2_mid": -95.79 } }
  },
  "notes": ["Region is a what-if: ...", "..."]
}
// row = { class, requests, candidate, effort, region, usd, wh_low, wh_high, j_mid,
//         gco2_mid, gco2_per_kwh, pass_rate }
```

Show `labels` next to every column. The region column and CO2 column always carry SIMULATED
while the grid is the fixture.

## Router decision (`wattnot route decide --json`)

```jsonc
{
  "label": "SIMULATED", "region_label": "SIMULATED",
  "chosen": { "id": "arith:medium@eu-north", "backend": "baseten/...", "effort": "medium",
              "region": "eu-north", "usd_per_req": 0.003473, "wh_low": 0.03703, "wh_high": 0.3703,
              "pass_rate": 1.0, "j_per_good_answer": 733.194,
              "usd_label": "SIMULATED", "energy_label": "ESTIMATE" },
  "reason": "medium in eu-north: passes the gate, 733.2 J per good answer; ...",
  "tie_broken_by_grid": true,
  "grid": { "region": "eu-north", "gco2_per_kwh": 35.0, "label": "SIMULATED", "source": "...",
            "as_of": "...", "note": "..." },       // null when the grid was not used
  "ranked": [ { "id": "...", "status": "excluded", "why": "failed: fixed" },
              { "id": "...", "status": "chosen" | "tied" | "passed",
                "j_per_good_answer": 733.2, "usd_per_req": 0.0035, "region": "...",
                "gco2_per_kwh": 380.0 } ]
}
```

`wattnot route grid --json` returns an array of `{region, gco2_per_kwh, label, source, as_of, note}`.

## Certificate (`wattnot certificate mint --json`, and `<serial>.json`)

```jsonc
// mint --json
{ "minted": true, "certificate": { /* below */ }, "files": ["certs/WNC-MOCK-0001.json", "..."],
  "register": "certs/register.json" }
// refused (exit code 2)
{ "minted": false, "reasons": [ { "code": "grade", "detail": "grade is NOISE; ..." } ] }
// codes: not_verified | grade | simulated | no_saving | ci_includes_zero | quality_drop
//        | no_joules | duplicate | register

// certificate
{
  "format": "wattnot.certificate/1",
  "status": "MOCK",                                   // always
  "watermark": { "lines": ["MOCK", "NOT TRADABLE", "NOT A CARBON CREDIT"], "permanent": true },
  "serial": "WNC-MOCK-0001", "issued_at": "2026-10-28T10:00:00+00:00",
  "title": "Verified Efficiency Statement (MOCK)",
  "receipt": { "receipt_id": "...", "sha256": "...", "spec": "wattnot.receipt/1",
               "grade": "ESTIMATE", "label": "MEASURED", "signer": "offline:...",
               "rekor": false, "created_at": "..." },
  "subject": { "model": "...", "provider": "...", "gpu": "...", "workload": "...", "mode": "..." },
  "period": { "receipt_created_at": "...", "requests": 200 },
  "headline": "Energy per request 9.9958% lower than the baseline (0.0048 Wh saved per request).",
  "figures": {
    "wh_per_request": { "baseline": 0.04802, "candidate": 0.04322, "saved": 0.0048,
                        "saved_ci95": { "low": 0.0046, "high": 0.005 },   // null for an ESTIMATE ratio
                        "baseline_range": null, "candidate_range": null }, // {low, high} for hosted runs
    "saving_pct": { "mean": 9.9958, "ci_low": 9.57, "ci_high": 10.42, "basis": null },
    "usd_per_request": { "baseline": 5.76e-06, "candidate": 5.19e-06, "saved": 5.7e-07,
                         "currency": "USD" },                              // or null
    "j_per_good_answer": { "baseline": 182.0, "candidate": 163.8, "change_pct": -9.9958 },
    "pass_rate": { "baseline": 0.95, "candidate": 0.95 }
  },
  "labels": { "grade": "ESTIMATE", "energy": "MEASURED", "basis": "ESTIMATE" },
  "verify": { "url": "https://github.com/ak9381/wattnot/tree/main/verifier#sha256=<receipt sha>",
              "qr_text": "<same>", "how": "..." },
  "limits": ["This is a mock. ..."],
  "integrity": { "canonicalization": "JCS RFC 8785", "sha256": "..." }
}
```

Always draw the three watermark lines from `watermark.lines`, and show `receipt.grade` as the chip
(SIMULATED when `labels.basis` is SIMULATED). Do not show a carbon figure; there is none.

`wattnot certificate verify <cert> [--receipt R] [--register REG] --json` returns
`{"ok": true, "serial": "...", "problems": []}`.

`wattnot certificate register --json` returns the register:

```jsonc
{ "format": "wattnot.cert-register/1", "label": "MOCK", "note": "...", "chain_ok": true,
  "problems": [],
  "entries": [ { "serial": "WNC-MOCK-0001", "minted_at": "...", "receipt_sha256": "...",
                 "claim_keys": ["receipt:<sha>", "run:<id>", "cloud:<sha>"],
                 "grade": "ESTIMATE", "label": "MOCK", "certificate_sha256": "...",
                 "prev_sha256": null, "integrity": { "sha256": "..." } } ] }
```

`wattnot certificate roadmap --json` returns `{format, label: "ADVICE", title, status, caveat,
items: [{need, why, wattnot_has, gap}]}`.

## Approvals (`wattnot proposals ...`, local API on `127.0.0.1:8781`)

Nothing WattNot wants to change is applied until a person approves it. Full behaviour is in
[`docs/APPROVALS.md`](../APPROVALS.md). The same JSON is on disk (`proposals/prop_*.json`,
`proposals/audit.jsonl`) and over HTTP from `wattnot proposals serve`. Reads need no token.
Every POST needs `Authorization: Bearer <token>` (`$WATTNOT_API_TOKEN`, or the one the server
prints at start-up). Errors are `{"code": "not_found" | "conflict" | "unauthorized" |
"bad_request" | "bad_host" | "apply_failed" | "bad_policy", "message": "..."}` with the matching HTTP status.

### Proposal (`wattnot.proposal/1`)

```jsonc
{
  "format": "wattnot.proposal/1",
  "id": "prop_20261027_070029_5103cc05",
  "kind": "power_cap" | "vllm_config" | "cloud_policy" | "flex_shed" | "moe_routing" | "trial_grant",
  "scope": "commit" | "trial",           // trial = changes are put back; only trial_grant
  "title": "Cap GPU 0 at 500 W",
  "summary": "Lower the power limit from 700 W to 500 W.",
  "created_at": "2026-10-27T07:00:29Z",
  "expires_at": "2026-10-28T07:00:29Z",
  "created_by": "wattnot",
  "source": { "tool": "wattnot cloud tune", "receipt_id": "..." },
  "label": "MEASURED",                   // the weakest label on any saving
  "change": {                             // what changes, from and to
    "gpu": 0,
    "before": { "power_cap_w": 700 },
    "after":  { "power_cap_w": 500 }
    // vllm_config: { target, before, after, changed: [...] }
    // cloud_policy: after = { effort_by_class, reference_effort, cache }
    // moe_routing: { target: "moe:<model>", before: { adaptive_topk: false },
    //                after: { adaptive_topk: true, adaptive_mass, model, implementation } }
    // trial_grant: { bounds: { power_cap_w: {min, max}, gpus: [..], ... }, restores_on_exit }
  },
  "savings": [ { "metric": "energy per good answer", "value": 18.0, "unit": "%",
                 "label": "MEASURED", "low": null, "high": null } ],
  "checks": {
    "quality": { "passed": true,  "detail": "..." },    // passed: true | false | null
    "p99":     { "passed": true,  "increase_pct": 4.2 }
  },
  "receipt_preview": {
    "profile": "gpu" | "cloud" | "flex", "label": "MEASURED", "expected_grade": "VALID",
    "will_claim": ["..."], "not_claimed": ["..."],
    "evidence_receipt": { "receipt_id": "...", "sha256": "...", "signed": true, "grade": "..." }
  },
  // not part of the reviewed content:
  "content_sha256": "9e3fa14b...",      // hash of the fields above (format ... receipt_preview)
  "status": "pending" | "approved" | "rejected" | "expired" | "applied" | "failed"
            | "rolled_back" | "closed",
  "decision":    null | { "by": "user:akash" | "pre-approved:NAME" | "auto-policy:<hash12>",
                          "at": "...", "via": "cli" | "http" | "pre-approved" | "auto-policy"
                          | "dry-run", "note": "", "content_sha256": "..." },
  "applied":     null | { "at": "...", "by": "...", "via": "...", "approved_by": "...",
                          "token": { /* what is needed to undo it */ }, "error": "..." },
  "rolled_back": null | { "at": "...", "by": "...", "via": "...", "note": "" },
  "receipt":     null | { "receipt_id": "...", "sha256": "<receipt integrity.sha256>",
                          "grade": "...", "signed": true, "linked_at": "..." },
  "policy":      null | { "policy_sha256": "..." }
}
```

`label` and every saving's `label` are MEASURED, ESTIMATE, SIMULATED or MOCK. Show them next
to the number. A frontend should show the diff from `change.before` to `change.after`, the
savings, both checks (a `null` check means it did not run), the receipt preview and the
expiry, and should send back the `content_sha256` it showed as `sha256` when approving.

### Audit entry (`audit.jsonl`, `GET /v1/audit`)

```jsonc
{ "seq": 2, "ts": "2026-10-27T07:00:29Z", "proposal_id": "prop_...",
  "event": "proposed" | "approved" | "rejected" | "applied" | "apply_failed" | "rolled_back"
           | "revoked" | "expired" | "receipt_linked" | "grant_opened" | "grant_closed",
  "actor": "user:akash", "via": "cli", "detail": { "note": "" },
  "content_sha256": "...",             // the proposal as it was reviewed
  "prev_sha256": "000...0",            // hash of the previous line; a chain
  "sha256": "..." }
```

### Endpoints

| Method and path | Returns |
|---|---|
| `GET /v1/health` | `{ ok: true, folder, pending }` (pending is a count) |
| `GET /v1/proposals?status=&kind=` | array of proposals |
| `GET /v1/proposals/{id}` | the proposal plus `audit: [entry, ...]` |
| `POST /v1/proposals/{id}/approve` | body `{by?, note?, sha256?, apply?}`; returns the proposal (status `approved`). Nothing is applied. |
| `POST /v1/proposals/{id}/reject` | body `{by?, note?}` |
| `POST /v1/proposals/{id}/apply` | applies an approved proposal on the machine running the server; 409 if not approved or expired; 409 `apply_failed` if the change could not be made or confirmed |
| `POST /v1/proposals/{id}/rollback` | undoes an applied change (revokes a live trial grant) |
| `GET /v1/audit?proposal_id=` | array of audit entries |
| `GET /v1/audit/verify` | `{ "ok": true, "entries": 4, "problems": [] }` |
| `GET /v1/policy` | the auto-approve policy, `sha256`, and `editable_here: false` |

### Auto-approve policy (`proposals/policy.json`, off unless enabled)

```jsonc
{ "format": "wattnot.auto-approve/1", "enabled": false, "allowed_labels": ["MEASURED"],
  "kinds": { "power_cap": { "min_saving_pct": 5, "max_p99_increase_pct": 10,
                            "max_cap_drop_pct": 25 } },
  "max_per_day": 5 }
```

`SIMULATED` and `MOCK` cannot appear in `allowed_labels`, and `trial_grant` cannot appear in
`kinds`. Every enabled kind needs `min_saving_pct` and `max_p99_increase_pct`; there are no
defaults.

### Flex receipts: the `approval` block

A flex receipt made under an approval carries
`"approval": { "proposal_id": "prop_...", "proposal_sha256": "<content_sha256>" }`, and the
proposal's `receipt.sha256` points back at the receipt's `integrity.sha256`.

### Exit code 50

`python -m wattnot.runbook`, `scripts/h100_oct30.sh`, `wattnot flex run --backend gpu` and the
setup probe exit **50** when nobody approved a session before the wait ran out. Nothing was
changed. `--pre-approved NAME` is the explicit flag for an unattended run.
