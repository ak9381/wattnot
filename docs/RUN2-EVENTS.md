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
| `agent_waste` | `waste.watch` … `waste.exit` | the Agent waste saver on the planning agent (Meta Muse Spark): repeat, no-op and cycle checks, a done action, step and token budgets; numbers come from the saved benchmark file |
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

## Resuming a run, and bounded SSH

`orchestrate` writes `OUT/checkpoint.json` after every stage and every sweep step (atomic write).
If the laptop, the network or the GPU session dies, run the same command again with `--resume`:

- finished stages (including `approve`) and finished cap steps are skipped, the run id and `events.jsonl` carry on
  (the event `seq` stays unbroken, no second `run.started`);
- a stage that was cut off in the middle is run again, except a command still going (or already
  finished) on the GPU host, which is re-attached and its log replayed quietly;
- a Muse proposal that was already asked for is reused, not asked (and paid for) again;
- if any setting that changes the measurement differs from the saved run, it refuses with
  `CheckpointError` instead of mixing two runs. `--resume` with no checkpoint starts fresh, so it
  is safe to put on every launch.

`--auto-resume N` does the same by itself: when the SSH link is gone for longer than
`--ssh-reconnect-budget-s` (default 600) the controller stops without a terminal event and is
restarted up to N times, 30 s apart.

SSH is bounded in three ways (RUN2 hung at 20:18 on a read that never returned): every exec has
one overall deadline (`--ssh-exec-timeout-s`, default 60) with per-read timeouts, TCP keepalives
and a 45 s user timeout are set on the socket, and the log poll redials after `--ssh-stall-s`
(default 240) of failed polls. A remote process that vanished is reported as exit 125.

## Measurement profiles (less stock-vs-stock noise)

RUN2's control showed 8.6% between two stock blocks (14.56 vs 15.81 J/req), while the 15 stock
blocks inside the cap steps agreed to 0.6% (sd/mean). The cause is not proven; a cold first
block is one possible cause. `--profile` sets everything at once and every field can still be
overridden by its own flag:

| profile | reps | repeat | warm-up | warm-up s | burn-in blocks | ABBA order | control stock blocks |
|---|---|---|---|---|---|---|---|
| `quick` (RUN2 settings) | 3 | 2 | 16 | 0 | 0 | off | 0 |
| `standard` (default) | 5 | 6 | 16 | 10 | 1 | on | 2 |
| `long` | 7 | 12 | 32 | 20 | 1 | on | 4 |

The control step now also reports `stock_noise_cv_pct` and `stock_range_pct` (MEASURED) from its
extra stock blocks, and each cap step carries a `stock_noise` figure. Longer profiles take
longer per cap step (`Profile.step_seconds` gives an ESTIMATE). Flags: `--reps --repeat --warmup --warmup-s --burn-in
--counterbalance/--no-counterbalance --control-blocks`.
