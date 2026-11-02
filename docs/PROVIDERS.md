# Providers: one flag, four profiles

Every install ships the whole framework. What changes per provider is what the platform lets us
touch, where each model runs, the caps, and which runner drives the round. All of that now sits in
one table, `src/wattnot/providers.py`, and one entry point:

```
python scripts/e2e/round.py --provider modal|baseten|h100|combined [--dry-run] [--smoke]
```

Without `--dry-run`, `round.py` only **prints** the live commands. It never starts paid work by
itself. With `--dry-run` it runs the CPU-only fake path, strips the Baseten keys and
`WATTNOT_MODAL_GO` from the environment first, and writes under `/tmp/wattnot-dry/`.
`tests/test_providers.py` fails if the table and the runner constants drift apart.

## The public round ("round 2")

Round 2 of each leg in `scripts/e2e/round-matrix.json`, run on the public data only:
GSM8K (first 60) + HumanEval (first 40) = 100 items per arm, without vs with WattNot.

| provider | round id | runner |
|---|---|---|
| baseten | r02 | `scripts/e2e/baseten_stage.py --rounds r02 --sets gsm8k humaneval` |
| modal | r05 | `scripts/e2e/modal_stage.py --rounds r05 --sets gsm8k humaneval` |
| h100 | r08 | `WATTNOT_PUBLIC_MATRIX_ROUND=r08 python -m wattnot.longrun.driver --live --plan public` |
| combined | r11 | the H100 public plan + the Baseten and Modal rows above, then per-class routing |

`--sets` now exists on every runner (baseten_round, modal_full_inner, modal_full_round,
modal/full_stage, both stage scripts). Leaving it out keeps the old behaviour (all three sets).

## What each provider does

**modal**: our vLLM + Muse Glimmer 30B on one A100-80GB.
- On: batch/KV sweep, FP8 candidate, prefix cache, thinking budget, request levers, eye, hearing, SAM, Wonder.
- Off: power caps. Modal refuses the NVML write (MEASURED in r04).
- Energy MEASURED (NVML counter).
- Caps: smoke 32 min / $1.50; full 225 min ceiling, $10 hard per round, $60 stage cap.
- Needs `WATTNOT_MODAL_GO=1` to start live work.

**baseten**: hosted `muse-spark-open` on the Model API, paid key `BASETEN_METRICS_KEY` only.
- No GPU access, so request levers only: effort routing (low first, step up to medium), context saver, agent waste guard.
- Text, grading and eye run on Baseten. Hearing, SAM 3.1 and Wonder run on the Meta API.
- Energy ESTIMATE (tokens -> FLOPs -> J).
- Caps: $8 hard per round, $1 smoke.

**h100**: our vLLM + Glimmer 30B on the E2E Networks H100 VM. Every lever, power caps included.
- Energy MEASURED.
- The `public` plan books 7 h (ESTIMATE). Budget cap Rs 3,258 = 7 h x Rs 451.2 + Rs 100 margin.
- `ctl.py` deletes the VM before the booking ends.

**combined**: runs the three public legs on the same prompts.
- `combine()` + `route()` pick, per class, the providers within 1 point of the best pass rate.
- Among those it picks the lowest $ per correct answer (ESTIMATE allocation).

## Baseten fixes in this change

- **Effort routing risk rule**: `approvals/risk.py` (from fix/h100-e2e-1101) has a REASONING_EFFORT rule. A per-request effort change is reversible and touches no hardware, so it is rated low-risk and goes through without waiting for a person. Before this, it fell into "unknown kind" and was rated high-risk.
- **Hearing ledger gap**: `e2e/live._transcribe` used to settle a 0-token, $0 entry. It now goes through `spokenanswer.client.transcribe_wav_live`, which charges the real per-minute price before the call and refunds it if the call is rejected.
- **Eye path**: Baseten uses the same `data/vision/eyetest` set, including the PIL-drawn gauge (answer 60). Numbers are checked exactly (abs < 1e-6), so 62 or 60.01 fails. A test covers this.
- **Token-cap fairness**: medium allowance raised to 12,000 for both arms, so the cap does not cut any final answer. Low keeps the 768 runaway cap and steps up. Each round doc now records `token_caps` and a `truncated_final_answers` count per arm.
- **Dry run**: `baseten_round.py --dry-run` uses canned MOCK replies. It makes no network calls, skips billing reads, and writes to `/tmp/wattnot-dry/baseten`.

## Fake output location

Modal `--fake` event files now go to `data/modal-full-1101/fake/` (gitignored), so they can no
longer overwrite the live `rNN.*.events.jsonl` files.

## Launch checklist (lessons from the Modal failures)

One $0 command per provider runs all of these before a paid launch. It makes no network call:

```
python scripts/e2e/preflight.py --provider baseten|h100|modal|combined
```

`baseten_stage.py` and `longrun.driver --live --plan public` run it themselves and refuse to start
(exit 3) if any check fails. `round.py --provider X` shows the result next to the live commands.
Tests: `tests/test_launch_check.py`, with one test per row plus negative cases for each check.

| # | Modal failure (source) | Baseten path | H100 path |
|---|---|---|---|
| 1 | agentwaste block rc 124 at the 900 s cap (r04-try1, `inner.json` failed.context_saver) | `block_time`: worst arm 38 min (ESTIMATE) vs the 3 h recorder limit; HTTP timeout 240 s x 2 retries | `block_time`: every model block's worst case must be <= 80% of its kill limit (hours x 1.5). The public plan trims agent waste to 8 cases x 1 rep x 768 tokens (30 min block), langchain 45 min, FP8 30 min, matrix 25 min. Plan 6.4 h vs 7 h booking |
| 2 | agent blocks need vLLM tool calling (2bcae4b) | hosted: `supports_tool_calling()` probe; never send `tools` to a model without it | `glimmer_serve.sh` has `--enable-auto-tool-choice --tool-call-parser muse_glimmer` |
| 3 | LangChain `/v1/v1` gave 502 (2bcae4b) | `cloud.chat_completions_url` for `/v1`, `/v1/` and a bare host | `langchain_pass.upstream_base`; also fixes `.../v1/` (trailing slash), which used to become `.../v` |
| 4 | Muse Code PR `KeyError: 'reasoning_effort'` (19014c2) | hosted PR spec builds; REASONING_EFFORT rated low | self-hosted PR spec (no effort key) builds |
| 5 | `No module named 'optuna'` (r04-try1 flow.log); uvicorn missing for the recorder | runner and dashboard venvs import optuna, uvicorn, fastapi, jsonschema, cryptography and langchain | `h100_vm_setup.sh` now installs the langchain pins (they were missing; p1-langchain would have failed) and import-checks optuna, uvicorn, fastapi, jsonschema and langchain right after install (exit 15) |
| 6 | recorder dashboard did not start, r04 aborted at 1:00 AM (2ad5549) | starts `wattnot.dash` under `WN_DASH_PY` on a spare port and waits for health 200. `baseten_stage` now uses `WN_DASH_PY` and stops the paid runner if the recorder crashes | same dashboard check (the combined clips use it) |
| 7 | gauge read 63 vs 60 (r04-try1 eye/block.log) | same eyetest set, exact check rejects 62, 63 and 60.5 | same |
| 8 | hearing sent at an assumed 16 kHz (443ce94) | header rate is read (24 kHz test clip); every clip's frames match its payload (no truncation or padding) | same, via `spokenanswer.client` |
| 9 | certificate step labelled MOCK (smoke-r04 flow.log) | canned MOCK replies only behind `--dry-run` | no-mock audit of the public plan |
| 10 | FP8 start overran (b726743, bdb2f25) | not applicable (hosted) | health wait 1,200 s + harness = 30 min worst vs 45 min kill |
| 11 | context saver failed through the agentwaste timeout (r04-try1) | saver is per request, timed on a 40-message history | GPU agent block sized by row 1 |
| 12 | `certificate mint` exit 2 on a NOISE receipt (smoke-r04) | NOISE vector -> exit 0 + not-issued statement | same |
| 13 | disk below 15 GB | box free disk >= 15 GB | driver `DISK_FREE_FLOOR_GB` >= 15 (aborts cleanly) + box check |

Time figures are ESTIMATEs. The token rate is MEASURED on Modal r04b (906 tokens in 32.05 s,
one stream), and each call is assumed to hit its token cap. The langchain rate is MEASURED on
Modal r04-try1 (20 items in 898 s) with a 30% margin added.

**Modal itself currently FAILS row 1.** The full round kills every block at 900 s. Langchain n=20
took 898 s in r04-try1 (2 s under the cap), and the agentwaste worst case is ~43 min (ESTIMATE).
Fix the Modal caps before the next Modal round.
