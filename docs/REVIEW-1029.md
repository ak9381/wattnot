# Pre-launch review — 2026-10-29

Full pre-launch review of WattNot ahead of the Meta hackathon deadline (Nov 4).
Scope: backend (`src/`, `scripts/`), frontend (`web/`), all user-facing copy, and a
static readiness audit of the 2-hour Glimmer H100 end-to-end test path.

Method: four parallel read-only reviews (backend correctness/security, frontend
JS/a11y/console, copy/UX, and H100 readiness), then fixes applied here with tests.
Nothing was launched; no network, paid, or GPU calls were made.

Final test status (run at end of review):

- `python -m pytest -q` → **1843 passed, 5 skipped**, exit code **0** (482s).
- `node tests/js/index.js` → **126 passed, 0 failed**, exit code **0**.

---

## Fixed (one commit per logical fix, each with a test)

| Commit | Fix | Why it mattered |
| --- | --- | --- |
| `1847b64` | `web/dash/fallback.js` reads the fetch target via a string/`Request.url`/`URL.href`/`String()` ladder (mirrors `static-api.js` `targetOf`) instead of a bare `u.url`. | On the live (non-static) dashboard, any receipt/transcript fetch issued with a `URL` object hit `undefined.startsWith(...)` and threw `TypeError`; the clip/receipt silently failed. New Node regression test (`tests/js/fallback.test.mjs`). |
| `f7ae845` | `web/run/index.html` icon-only controls (`zin/zout/fit/dclose/back/play/fwd`) each gained an `aria-label`. | Icon buttons had no text and relied on `title` alone, which screen readers expose unreliably. New test (`tests/js/run_a11y.test.mjs`) asserts every icon-only control has an `aria-label`. |
| `7f5212f` | `src/wattnot/cloud/modal_url.py` `_from_cache` now catches `(OSError, ValueError)`. | A cache file with invalid UTF-8 raised `UnicodeDecodeError` and escaped `resolve_modal_url` instead of falling through to the clear `ModalUrlUnset`. New test with a corrupt cache file. |
| `415b186` | `src/wattnot/moe/adaptive_topk.py` `summarize()` raises `AdaptiveError` when the baseline ("off") arm has zero joules/token or zero p99. | A degenerate/fake arm with no measured energy made the saving/p99 denominator 0 → bare `ZeroDivisionError`. Now a clear error. New test. |
| `2199c72` | `README.md` judges' headline: "fixed answer checks from the Granite era" → "fixed answer checks (deterministic string and number matching, no model as a judge)". | Opaque jargon for judges. No numbers or claims changed. |

### Confirmed clean during the review (not changed)

- **No competitor AI-company names anywhere.** A repo-wide scan for Claude, Anthropic,
  Grok, Gemini, Antigravity, ChatGPT found **zero** occurrences in `src/`, `scripts/`,
  `web/`, `README.md`, `SPEC.md`, `docs/`. `openai` appears only as the OpenAI-compatible
  SDK/`base_url` interop identifier (how a user points their existing client at the local
  vLLM endpoint) — a legitimate integration name, not competitor marketing copy.
- **Llama, SAM 3.1, Qwen3, Granite are intentional and honest, not violations.** This is a
  *Meta* entry: "Powered by Meta" with Llama/SAM 3.1 is the point. Qwen3 and Granite are
  real non-Meta models that were genuinely benchmarked on the H100 (MEASURED NVML
  receipts); the code discloses this explicitly (`src/wattnot/dash/runplan.py:153,729`:
  "Qwen3 and Granite are not Meta models … history only"). Renaming any of these to
  fictional names would **break** the MEASURED-honesty principle, so they were left as-is.
  None are in the specified banned list (which is competitor labs only).
- **Water numbers consistent:** headline **0.36 L/kWh** (on-site cooling), **1.8 L/kWh**
  upper bound (incl. power-plant water) everywhere they appear (web, README, docs, `impact.py`).
- **Evidence labels honest and consistent:** MEASURED / ESTIMATE / SIMULATED / MOCK /
  REPLAY / ADVICE used correctly; the fake-backend path reports SIMULATED and never claims
  MEASURED; cost/water are tagged ESTIMATE (derived from measured joules); room heat SIMULATED.
- **No hardcoded secrets.** Keys are read from env/keychain (`META_API_KEY`, `E2E_API_KEY`/
  `E2E_API_TOKEN`, `HF_TOKEN`) and never logged, printed, or written to ledgers/receipts.
- **No unsafe dynamic execution.** No `shell=True`, no `os.system`; the two `eval` uses are
  regex-whitelisted with empty `__builtins__`; the untrusted-code sandbox uses `python -I`,
  RLIMIT caps, a network-block audit hook, write-path jailing, and a wall-clock group kill.
- **Frontend links/assets resolve**, `<html lang>` present on all pages, images have alt
  text, dialogs use `role`/`aria-modal`, contrast tokens pass 4.5:1 (`contrast.test.mjs`).

---

## Backlog (left for after launch; not blocking the demo)

Ranked by value. None are correctness bugs on the demo (replay) path.

1. **`meta/ledger.settle` "ok-no-usage" entries keep the worst-case reserve estimate**
   (`src/wattnot/meta/client.py:99-101`, `ledger.py:246-257`). A 200 response with no
   `usage` block is never reconciled, so the ledger over-counts that call at the reserve
   price. This is the *conservative/safe* direction (it trips the budget cap early, never
   under-counts), so it is left as-is; but it reduces displayed spend accuracy. Decide a
   policy (recompute from available counts, or zero it) and reconcile.
2. **Longrun watchdog budget/time caps are unarmed unless the operator passes them**
   (`src/wattnot/longrun/watchdog/cli.py:65-67`, `agent.py`, `local.py`,
   `scripts/mac_watchdog/watchdog.py:280`). `--budget-cap-inr`/`--max-hours` default to
   `None` = not enforced. For the 2h E2E test the *controller's own* watchdog (`ctl.py`)
   auto-arms the time + balance caps, so this is not blocking — but the off-box layers
   should require at least one cap on a live run.
3. **Driver enforces the spend/time rule only between blocks, not within one**
   (`src/wattnot/longrun/driver.py:276-283`). A single hung block can overrun `max_hours`
   until the next check; the watchdog layers are the intended backstop. Add an in-block
   `timeout_s`.
4. **Approvals read API is unauthenticated and bindable non-loopback**
   (`src/wattnot/approvals/server.py:179-193`). `GET` routes (proposals, audit, policy)
   have no auth and `serve --host 0.0.0.0` is permitted. On a shared host this exposes the
   proposal/audit trail. Refuse non-loopback binds without an explicit opt-in, or require
   the token on reads too.
5. **Sandbox reads child stdout/stderr only after `os.wait4`**
   (`src/wattnot/agentwaste/sandbox/runner.py:182-186`). A child writing >~64 KB fills the
   pipe and the run is misreported as `timed_out` with truncated output (the wall-clock
   group kill still prevents a true hang). Drain pipes concurrently.
6. **`LocalTransport.run` timeout only fires when a line is produced**
   (`src/wattnot/proof/transport.py:80-87`). A silent child is never killed. Limited blast
   radius (rehearsal/dry path only; `SshTransport` handles timeouts correctly).
7. **`ledger.total()`/`summary()` raise `KeyError` on a hand-edited/legacy malformed entry**
   (`src/wattnot/meta/ledger.py:182-203`). Use `.get(...)` defaults or validate entry shape.
8. **`/api/verify` buffers the whole body before the 1 MB size check**
   (`src/wattnot/api/app.py:338-340`). Minor DoS surface; check length while streaming.

Copy follow-ups (product decision, not a defect):

9. **README line 9** states "Every model WattNot runs is a Meta model (Glimmer, Llama,
   Spark, SAM 3.1, Wonder)." The Qwen3/Granite benchmarks are non-Meta (and disclosed as
   such elsewhere). The live/recorded run uses Meta models; consider softening to "Every
   model in the recorded run is a Meta model" to match `runplan.py`'s own disclosure.

---

## Glimmer H100 2-hour test — GO / NO-GO

**Verdict: GO WITH CONDITIONS.**

### Why GO

- **The 2h wall-clock bound is a real hard kill.** `scripts/e2e/ctl.py` persists
  `deadline = now + MAX_H h` (`MAX_H=2`) at launch and auto-spawns a detached watchdog that
  deletes the VM at `deadline − 8 min`, polling every 120 s (`ctl.py:159,163-164,182-191`).
  The teardown is wired in and armed automatically, not advisory.
- **Paid/GPU actions are gated behind explicit flags, never default-on.** Launch requires
  `RUN3_GO=1` (`ctl.py:107`); the Modal paid path requires `WATTNOT_MODAL_GO=1`; the real
  H100 run requires `--pre-approved` or a human approval (`h100_run.sh:13-15`,
  `h100_oct30.sh:19-26`), else it exits without changing anything. `--dry-run`/`--fake`
  produce only SIMULATED numbers.
- **Cost is bounded.** Launch asserts the price (`<= 379.05`) and a sufficient prepaid
  balance (`2*451.2 + 1500` INR, `ctl.py:146,149-150`); the watchdog tears down if the
  balance drops below the ₹1500 floor (`ctl.py:188`). Worst-case uncontrolled spend for a
  2h test is small (~₹900) because the time cap dominates. The separate `$98`/effective
  `$96` ledger cap (`src/wattnot/spend/guard.py`) guards the hosted Baseten/Modal paths with
  an fsynced, append-only, crash-resumable ledger.
- **Receipts sign offline with no secrets.** Ed25519 key on disk, `--no-rekor`; a missing
  key yields an `UNSIGNED` receipt rather than a crash; public/Rekor signing is firewalled
  behind `WATTNOT_PUBLIC_SIGN_APPROVED=1` and not fully implemented. Real vs simulated is
  enforced by a label gate (`h100_oct30.sh:97-108`: every receipt must be MEASURED on a real
  run, SIMULATED on `--fake`; exit 40 on mismatch).
- **Safe defaults everywhere:** mac watchdog defaults to `--dry-run`; the in-repo longrun
  watchdog CLI cannot delete at all (hard `SystemExit` stub); `ctl.py` is import-safe and
  holds no hardcoded secrets/IPs (enforced by `tests/test_e2e_ctl.py`).

### Conditions (must confirm before launch)

1. **Confirm the live control-host controller matches the audited logic.** `ctl.py` writes
   state to `/workspace/hackathon/e2e-glimmer-test` and imports a `post` helper from
   `/workspace/e2e` (`ctl.py:24,31`) and reads `~/.ssh/e2e_h100.pub` (`ctl.py:138`). Verify
   that control-host copy, the `post` helper on `PYTHONPATH`, and the SSH pubkey exist and
   are the same logic reviewed here. **If this cannot be confirmed, it is a NO-GO** — a
   divergent uncommitted copy could run without the safety rails.
2. **Confirm `RUN3_MAX_H` is unset** (so `MAX_H=2`) and `RUN3_GO=1` is set only at the moment
   of an approved launch (`ctl.py:35,107`).
3. **Confirm `E2E_API_KEY`/`E2E_API_TOKEN` and `HF_TOKEN` are in the environment, not the
   repo**, and prepaid balance ≥ ₹2402 (launch gate).
4. **After `launch`, confirm `vm.json` has a deadline ≈ now+2h and the watchdog child is
   alive** (`wd.out` is being written) — this is the single armed teardown.
5. **Recommended backstop (not blocking for a 2h run):** the only armed teardown is a
   detached child of the launching shell; if that host dies mid-run nothing deletes the VM
   (off-box layers 4/5 are intentionally disabled and their E2E auth is unverified). Arm one
   independent backstop — e.g. on-VM `sudo shutdown -h +118`, or a provider-side TTL — so a
   control-box failure still bounds the bill.
6. **Run the GPU side once as `h100_run.sh --dry-run` first** to confirm SIMULATED receipts,
   then the real run only with `HF_TOKEN` + `--pre-approved`.

### Eye test (vision quality gate) in the 2h plan

The vision quality gate runs inside the 2h Glimmer test, once per candidate, on a 90 s
wall-clock budget (`EYE_TEST_BUDGET_S`, `longrun/driver.py`). It is a first-class step in
`plan_glimmer_test` — `t1-eye` after BF16 and `t2-eye` after the FP8 restart — so a
quantisation that quietly breaks vision is caught even when the text checks pass. The
served vision model (Glimmer, or Spark via the Meta Model API) answers the pre-generated
SYNTHETIC images in `data/vision/eyetest`; any miss fails the block, rejects the change,
blocks the Muse Code PR, records the reason on the signed receipt
(`measurement.eye_test`), and rolls back to the last safe config through the existing
`decide`/`guarded_apply` loop. The images are cached, so it is seconds of served
inference, not the 13 s generation hot path. No launch and no paid call: dry runs use the
offline answerer (`wattnot.vision.run --fake`); the one-time paid image generation lives
only in `scripts/gen_vision_assets.py` (needs `WATTNOT_GEN_VISION_GO=1`, never run by
tests). Every generated image carries an ESTIMATE energy stamp.

---

## Vision gate review (2026-10-30) — GO

Branch reviewed: `feat/vision-gate-1030` (eye test, SAM synthetic hotspot set, Wonder reads
the chart, watts per spoken answer, talk to the tuner, review fixes). Merged into main from
`feat/vision-gate-1030-clean`, which has the same code with tool trailers removed from three
commit messages.

- Schemas: `schemas/` matches `docs/schemas/` for every file this branch touched;
  `scripts/gen_ts_types.py --check` reports `web/types/schemas.ts` up to date.
- Labels: new panels use SYNTHETIC (SAM hotspots, spoken questions) and ESTIMATE. One fix:
  the tuner dry run was labelled MOCK; it is now SIMULATED and its text and speech say it
  assumes the quality checks pass (the dry run submits with `gates_passed=True`).
- Frontend: hotspot set, Wonder chart and spoken answer panels are imported in
  `report.js`, CSS linked in `report.html`; the tuner plan renders through `voiceguide.js`.
- No secrets and no AI-company names in the diff (`openai` only as the OpenAI-compatible
  API name).
- Integration run: `python -m pytest -q` exit 0; `node tests/js/index.js` 163 passed, 0 failed.

Verdict: **GO**.
