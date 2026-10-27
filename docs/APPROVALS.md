# Approve before apply

WattNot never changes your hardware or your serving settings on its own. When it finds
something worth changing, it writes a **proposal** and stops. A person reads it, and only then
does anything move.

This covers every change WattNot can make:

| kind | what it changes |
|---|---|
| `power_cap` | a GPU power limit |
| `vllm_config` | vLLM batch size, KV cache and quantization settings |
| `cloud_policy` | hosted-model effort and cache routing (Baseten) |
| `flex_shed` | cut GPU draw for a grid event |
| `moe_routing` | MoE routing knob: adaptive top-k (off unless approved; see [ADAPTIVE-TOPK.md](ADAPTIVE-TOPK.md)) |
| `trial_grant` | permission to change things temporarily, inside bounds, to take measurements |

Everything is local files plus Python. No cloud service, no account.

## A proposal at a glance

```
$ wattnot proposals show prop_20261027_070029_5103cc05
Cap GPU 0 at 500 W
  id        prop_20261027_070029_5103cc05
  status    PENDING
  kind      power_cap (commit)
  label     MEASURED
  expires   2026-10-28T07:00:29Z
  hash      9e3fa14ba125406f8e11f42b52425255fe61cee2174c6fb2bdca8121d46849d6

Lower the power limit from 700 W to 500 W.

What changes:
  power_cap_w: 700 -> 500

Savings:
  energy per good answer: 18.0%  [MEASURED]

Checks:
  quality  PASS
  p99      PASS  +4.2 %

Receipt preview:
  profile gpu, label MEASURED, expected grade VALID
  will claim: ...
  not claimed: ...

Audit trail:
  #1 2026-10-27T07:00:29Z proposed       wattnot via system
```

It holds, in this order: what changes (from and to), the saving with its label, the quality
and p99 check results, a preview of the receipt it will be tied to, and an expiry.

Labels are the same everywhere in WattNot: `MEASURED`, `ESTIMATE`, `SIMULATED`, `MOCK`. A
proposal's overall label is the weakest one on any of its savings. A proposal built from a
simulated run says `SIMULATED` and cannot be mistaken for a real result.

WattNot only files a proposal when the quality gate and the p99 gate passed in the
comparison that produced it. A failed comparison is a result, not a proposal.

## Life of a proposal

```
pending --approve--> approved --apply--> applied --rollback--> rolled_back
   |                    |                   |
   |                    |                   `--(apply fails)--> failed
   |--reject--> rejected
   `--(time runs out)--> expired
```

Trial grants end in `closed` instead of `rolled_back` (see below).

- **approve** records who and when. It changes nothing on the machine.
- **apply** is a separate step, run on the machine that owns the hardware. Use
  `approve --apply` to do both when you are sitting at that machine.
- **expire**: a proposal that is still `pending` or `approved` (not yet applied) goes stale
  after 24 hours (a proposer can set a different `ttl_s`). Any command that reads or changes
  the store marks overdue ones `expired`, and `wattnot proposals expire` does it on demand. An
  expired proposal cannot be approved or applied. Make a new one.
- **rollback** undoes an applied change using the token saved when it was applied: the old power
  cap is written back, or the previous config file is restored.

## Commands

```
wattnot proposals list [--status pending] [--kind power_cap] [--json]
wattnot proposals show <id> [--json]
wattnot proposals approve <id> [--by NAME] [--note TEXT] [--sha256 HASH] [--apply]
wattnot proposals reject <id> [--by NAME] [--note TEXT]
wattnot proposals apply <id>
wattnot proposals rollback <id> [--note TEXT]
wattnot proposals audit [<id>] [--json]
wattnot proposals verify
wattnot proposals expire
wattnot proposals link-receipt <id> <receipt.json>
wattnot proposals policy show | init
wattnot proposals serve [--host 127.0.0.1] [--port 8781]
```

- Every command takes `--dir`. The default is `$WATTNOT_PROPOSALS_DIR`, else `./proposals`.
- An id can be shortened to any unique prefix.
- `--by` defaults to `$WATTNOT_APPROVER`, else your login name. It is recorded as `user:<name>`.
- `--sha256` is optional but worth using from scripts: pass the `content_sha256` you reviewed
  and the approval is refused if the proposal is not exactly that.
- A power-cap proposal can only be applied on a machine with the GPU (and NVML). Elsewhere
  `apply` fails with a clear message and the proposal stays `approved`.

## What gets stored

```
proposals/
  prop_<time>_<id>.json   one file per proposal (the current state)
  audit.jsonl             append-only audit trail, one JSON line per event
  active/                 the settings last applied (vllm_config.json, cloud_policy.json)
  policy.json             the optional auto-approve policy (absent = off)
```

`proposals/` is in `.gitignore`. It is local state, not source.

### The reviewed content cannot change

When a proposal is created, the fields a person reviews are hashed into `content_sha256`. Those
fields are never edited afterwards. Later facts (decision, apply result, receipt link) live in
separate fields and in the audit trail. So "what did Akash approve?" always has one
answer: the content with that hash.

### The audit trail

Each line of `audit.jsonl` records the time, the proposal, the event (`proposed`, `approved`,
`rejected`, `applied`, `apply_failed`, `rolled_back`, `expired`, `receipt_linked`, ...), who did
it (`user:<name>`, `pre-approved:<name>`, `auto-policy:<hash>`, or `wattnot`), how (`cli`,
`http`, ...), the proposal's `content_sha256`, and the hash of the line before it. The lines
form a hash chain.

`wattnot proposals verify` walks the chain and reports:

- a line that was edited,
- a line that was removed or reordered,
- a proposal whose recorded status the audit trail does not explain (for example a
  proposal file edited by hand to say `approved`).

It exits 0 when the trail is intact. This is tamper-evidence on one machine. It is not a
signature: someone who can rewrite the whole folder can rewrite the chain. The receipts are
what you sign and publish.

### Link to the signed receipt

A proposal is tied to the receipt that proves the saving, by the receipt's
`integrity.sha256`:

- If the evidence receipt already exists when the proposal is made (the usual case for
  `wattnot cloud tune`), its hash is in `receipt_preview.evidence_receipt` and is linked
  when the proposal is applied.
- A receipt written later is linked with `wattnot proposals link-receipt <id> <receipt.json>`.
- Flex receipts written under an approval carry an `approval` block with the proposal id and
  `content_sha256`, so the link works in both directions: the receipt names the proposal, the
  proposal names the receipt.

The receipt's own hash and signature rules are unchanged.

## Trial grants: changes that are put back

Finding a good power cap means trying caps. Asking a person about every one of 60 trial writes
would make the tool unusable, and each one is put back anyway. So a session asks once, for a
**trial grant**: permission to change named things, inside stated bounds, for a limited time,
with everything restored at the end.

A grant says, for example, "GPU power cap between the card's minimum and maximum, on GPUs 0-1,
and restart vLLM with these models, until 14:00". Inside it WattNot may do those things and
nothing else. A write outside the bounds is refused. A revoked or expired grant refuses every
further write; restoring the original settings is always allowed.

- Grants are never auto-approved.
- A grant keeps counters (how many changes, of which kinds). They are in the audit trail when
  the grant closes.
- The runbook asks for a grant before its first change and closes it after it has put
  every cap back.
- Revoke a live one with `wattnot proposals rollback <grant id>`.

What the trial may *leave behind* is a different question. The best cap or config it finds
is filed as an ordinary commit proposal, which still needs a person.

## The unattended session: `--pre-approved`

The H100 session on Oct 30 runs unattended, so nobody is there to type `approve`. For that
there is one explicit flag. Passing it is the approval, made in advance by whoever launched
the run:

```
bash scripts/h100_oct30.sh --pre-approved --approved-by Akash
```

- The grant is recorded as approved by `pre-approved:h100_oct30.sh (by Akash)`. It is visibly
  not a click on a proposal.
- It covers the trial only: power-cap changes inside the card's range on the GPUs in use, the
  vLLM restarts for the sweep, and the flex events. All of it is restored at the end.
- It does not apply any commit proposal. The best settings it finds are left `pending`.

Without the flag, the run files the grant, waits (`--approval-wait-s`, default 600 seconds)
for someone to run `wattnot proposals approve <id>` on the box, and if nobody does, exits
with **50**: nobody approved, nothing was changed.

Exit codes of `scripts/h100_oct30.sh` are unchanged (0 ok, 10 setup, 20 probe, 30 a later stage,
40 receipt labels not as expected) plus the new 50.

`--fake` still works with no GPU and no approval. The simulator rig uses a throwaway dry-run
gate, which may only approve things labelled `SIMULATED` or `MOCK` and can never drive a
real card. Its proposals and audit trail land in the results folder, under `proposals/`.

## Auto-approve policy (optional, off by default)

If you want small, boring, well-measured changes to go through without a click, you can say
so. It is off unless you create the file and turn it on:

```
wattnot proposals policy init       # writes proposals/policy.json with enabled: false
```

```jsonc
{
  "format": "wattnot.auto-approve/1",
  "enabled": true,
  "allowed_labels": ["MEASURED"],
  "kinds": {
    "power_cap":    { "min_saving_pct": 5,  "max_p99_increase_pct": 10, "max_cap_drop_pct": 25 },
    "cloud_policy": { "min_saving_pct": 20, "max_p99_increase_pct": 10 }
  },
  "max_per_day": 5
}
```

A proposal is approved by the policy only if all of this holds; otherwise it stays pending:

- the policy is enabled and the proposal's kind is listed (unlisted kinds are never auto-approved),
- every saving carries a label in `allowed_labels`. `SIMULATED` and `MOCK` cannot be listed,
- the quality check ran and passed, and the p99 check ran and passed,
- the saving is at least `min_saving_pct`, and the p99 rise is at most `max_p99_increase_pct`,
- for power caps, the drop is at most `max_cap_drop_pct`,
- fewer than `max_per_day` have been auto-approved today.

There are no default thresholds. Each enabled kind must set `min_saving_pct` and
`max_p99_increase_pct`, or the policy file is rejected. A policy cannot list trial grants at all
(the file is rejected), and a flex event is only auto-approved if its kind is listed (use
`max_target_pct` to cap how deep a shed it may be). Auto-approvals are recorded as
`auto-policy:<first 12 characters of the policy file's hash>` in the audit trail, with the
reasons that held, so you can tell which policy version did it.

## Risk levels and the dashboard

Not every change needs a click. The guardrail rates each proposal first
(`wattnot.approvals.risk.classify`). The rules are fail-safe: anything not
clearly low is high.

- A power cap is low only when it is within `low_cap_drop_pct` (default 15) of
  stock, not below `high_below_tdp_pct` (default 70) of the card's TDP, and it
  touches at most `max_gpus_auto` (default 1) GPUs. Raising a cap is low.
  Missing numbers mean high.
- A vLLM tweak is low unless it switches quantization. Routing, shed and trial
  grants are always high. A cloud policy is low (it touches no hardware).
- The cutoffs live in `proposals/guardrail.json` (`wattnot.guardrail/1`, see
  [guardrail.example.json](guardrail.example.json)). A missing file means the
  defaults. Low-risk proposals are approved at once as `guardrail:low-risk`;
  the rest stay `pending` for `approval_timeout_s` (default 600) seconds.

Timeout is reject. A proposal nobody decides in time becomes `expired`, and an
expired proposal cannot be approved or applied. The receipt calls this a reject
by `system:timeout`.

`--auto-approve` is for unattended demos only. High-risk changes are approved
at once, logged as `auto-approve-flag:<name>` via `pre-approved`: visibly not
a person's decision. The flag is on the orchestrate run, the dashboard
(`--auto-approve`), or a `Guard(auto_approve_by=...)` you build yourself.

### Autonomy tiers

Each planner has a tier in `proposals/guardrail.json` (`tiers` maps a planner
name to `standard` or `strict`; a name not listed is `strict`):

- `muse-spark` and `bisect` are `standard`: the rules above apply unchanged.
- `muse-spark-open` (the open-weights planner on Baseten) is `strict`: it may
  only auto-apply LOW risk. Its high-risk changes stay `pending` (route
  `pending-strict`) and `--auto-approve` does not apply to them. The audit
  note on the proposal and the risk reasons both say so: "proposed by an
  open-weights planner: needs a person". Low-risk changes from a strict
  planner still auto-apply once the gates pass.

The planner (`name`, `model`, `partner`, `tier`) is stored under
`source.planner`, so it is part of the hashed content the approver sees. The
dashboard cards (`GET /api/dash/approvals`), the receipt ops and the proof
receipt's top-level `planner` block (`partner`, `tier` added) all carry it.

One click undoes an applied change (`rollback`, or the dashboard's undo). The
stored token puts the old cap or config file back. An undo after the receipt
is signed cannot rewrite the signed file, so it is written beside it as
`<proposal id>.undo.json` (`wattnot.guardrail-addendum/1`): the receipt it
belongs to, the undo op, its own sha256, and an offline signature when a key
is given.

The dashboard serves the same store (mock caps unless `--live-apply`):

```
GET  /api/dash/approvals   pending, applied and recent cards, the config, mock or live
POST /api/dash/approve     body {"id", "by"?, "sha256"?, "apply"?}: decide, then apply only with "apply": true
POST /api/dash/reject      body {"id", "by"?, "note"?}
POST /api/dash/undo/{id}   put the old setting back
```

The three POSTs need `Content-Type: application/json` (a plain cross-site form
gets 415) and sit behind the dashboard token when one is set. Errors are
`{"code", "message"}`: 404 unknown id, 409 wrong state.

## Local HTTP API

For the future frontend. `wattnot proposals serve` runs it with FastAPI on `127.0.0.1:8781`.

```
GET  /v1/health
GET  /v1/proposals?status=&kind=
GET  /v1/proposals/{id}              the proposal plus its audit trail
POST /v1/proposals/{id}/approve      body: {"by": "...", "note": "...", "sha256": "..."}
POST /v1/proposals/{id}/reject
POST /v1/proposals/{id}/apply
POST /v1/proposals/{id}/rollback
GET  /v1/audit?proposal_id=
GET  /v1/audit/verify
GET  /v1/policy                      read-only; edit the file, not the API
```

Shapes are in [`docs/moonshot/API-SHAPES.md`](moonshot/API-SHAPES.md).

It is made for one machine, so it is careful in small ways:

- Reads are open on localhost. Every POST needs `Authorization: Bearer <token>`. The token is
  `$WATTNOT_API_TOKEN`, or a random one printed once when the server starts. Without a token
  the app will not start.
- Requests whose `Host` header is not local are refused (this blocks DNS rebinding).
- CORS allows only `localhost:3000` and `localhost:5173` by default.
- The server binds to `127.0.0.1`. If you point `--host` elsewhere it warns. It has no
  user accounts, so do not put it on a network.
- Errors are JSON: `{"code": "not_found" | "conflict" | "unauthorized" | ..., "message": "..."}`.

## Where the gates are

| Path | What happens |
|---|---|
| `scripts/h100_oct30.sh`, `wattnot.runbook` | asks for a trial grant (or `--pre-approved`) before the first change; files the best settings as pending proposals |
| `scripts/h100_oct30.sh --adaptive-topk` | the trial needs a `moe_routing` bound in the grant (a mass range, default 0.5 to 0.999); a passing result is filed as a pending `moe_routing` proposal, never applied by the run. See [ADAPTIVE-TOPK.md](ADAPTIVE-TOPK.md) |
| `wattnot flex run --backend gpu` | files a `flex_shed` proposal and waits; applies only the approved cap; restores after |
| `wattnot cloud tune` | files a pending `cloud_policy` proposal when the tuned policy passed its gates (`--no-propose` to skip) |
| `wattnot.probe` (setup probe) | the one cap write it does is gated the same way |
| `CapGuard` (NVML) | takes a `permit` callback: it is asked before every write, and restore does not ask |
| `flex run` on the simulator, `--fake` runs | no approval needed; nothing touches hardware; labelled SIMULATED |

## What this does not do (yet)

- Library functions such as `CapGuard` and the `Approvals` applier only enforce approval
  when the caller passes a `permit`. The entry points above all do. If you write your own
  script that calls NVML directly, you are outside the gate. This is a guard rail for the
  tool, not an OS-level sandbox.
- `config` proposals write `active/<kind>.json`. WattNot does not restart a running vLLM
  or change a live Baseten route. Whatever serves traffic should read the file with
  `wattnot.approvals.active.read_active(kind)`.
- The audit trail is hash-chained on one machine, not signed or timestamped by a third party.
- The HTTP API has one shared token, no per-person login. `by` in a request body is
  recorded as given.
- Auto-approve has been tested with fakes only. Do not turn it on for real hardware before
  you have read a few real proposals.
- A killed process mid-apply can leave a proposal `approved` with a half-done change; the
  readback after a power-cap write catches a wrong value, and `rollback` on an applied
  proposal puts the old cap back. A stuck `applied` grant is closed by revoking it.
