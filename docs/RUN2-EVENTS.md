# RUN2 event stream

`python -m wattnot.proof.orchestrate` runs the whole proof from the control machine and writes
one `wattnot.event/1` stream (`runs/<run_id>/events.jsonl`, mirrored to `--live-file`). The GPU
host prints raw `EVT` lines; the control machine validates them, numbers them and writes them.
Point the dashboard at the live file: `python -m wattnot.dash --events <live file>`. It follows
the file as it grows, and the same file is the replay.

Every step is a `step.started` / `step.finished` pair with `data.node` and `data.sub` (the
architecture-map node and sub-step), `label`, `inputs`, `narration`, and on finish `outputs`,
`duration_s`, `numbers[{label, v, unit, tag}]`. Sub-steps have `parent_step_id` set.

| step_id | map node.sub | what it is |
|---|---|---|
| `room_scan` | `sam.masks` | SAM 3.1 room scan, the H100 in rack R03 (carries `data.kind == "room_scan"` progress payloads, before and after) |
| `baseline` | `workload.send` | 111-prompt answer gate at the stock cap |
| `quality_gate` | `gates.decide` | does the gate reject a deliberately cut-short setting |
| `cap_sweep` | `e2e_rig.sweep` | the planner proposes (Muse Spark, or Muse Spark Open with `--planner baseten-open`), interleaved stock/cap blocks decide; sub-steps `propose<N>` (`agent.call`) and `cap<W>` |
| `adaptive_topk` | `adaptive.arms` | agreement %, J/request with fewer experts per token |
| `approve` | `approve.classify` / `approve.decide` | the risk rating and the wait for an operator |
| `keep_rollback` | `apply.keep` | the chosen cap applied and read back, or rolled back |
| `receipt` | `receipts.build` | canonical JSON, SHA-256, offline signature, grade |

Labels: MEASURED (live card), MOCK (rehearsal on the fake meter), SIMULATED (other racks, the
photo), REPLAY (saved SAM output), ADVICE (planner/room hints), ESTIMATE.

Each `trial.proposed` and `decision` in the sweep carries `planner` (`name`,
`model`, `partner`: `meta` for Muse Spark, `baseten` for Muse Spark Open,
null for bisect/grid). The narration `source` on the agent's progress events
is `muse-spark` or `muse-spark-open` when that planner proposed, else
`template`.

Rehearsal (no GPU, no API calls):
`python -m wattnot.proof.orchestrate --mode rehearsal --out OUT --live-file OUT/live/run.events.jsonl`

With `--planner baseten-open` the same scripted answers play through the open
planner (MOCK, no network): proposal events and the receipt say
`muse-spark-open` / `baseten` / tier `strict`.

## The approve step

Between the sweep and the keep sits `approve`: `approve.classify` rates the
risk (low or high, with reasons), `approve.decide` waits for an operator.
A high-risk change that nobody decides in time stays unapplied: the run emits
the usual `action.rolled_back` with `failed_gates` of `["approval timed out"]`
(or `["operator rejected the change"]`) and carries on to the receipt.

While a change waits, the run emits one `proposal.created` event:

```
{"change_id": "prop_...", "risk": {"level": "high", "reasons": [...]}, "expires_at": "2026-10-27T...Z"}
```

The dashboard reads it as a pending card. `--auto-approve` skips the wait:
the change is approved at once, logged as `auto-approve-flag:orchestrate`
(`pre-approved`), visibly not a person's decision. In rehearsal the whole
approve block is labelled MOCK, like everything else on the fake meter.

The approve step's inputs name the planner and its tier; `approve.decide`
numbers carry them too (`planner` and `planner tier`, ADVICE). A high-risk
change from a strict-tier planner stays `pending-strict` even with
`--auto-approve`: with nobody to click it, `--approval-timeout-s` runs out
and the run rolls back, carrying on to the receipt.
