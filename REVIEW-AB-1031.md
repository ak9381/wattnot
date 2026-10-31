# A/B fairness review — with-WattNot vs stock (retest + long run)

Branch `review/ab-fairness-1031` off `origin/main` (2552b37). Scope: confirm the
with-WattNot vs without-WattNot (stock) comparison is ready and FAIR for the retest
(`python -m wattnot.longrun.driver --live --model glimmer-test --plan retest --max-hours 2.0`,
including the combined block: best cap + prefix caching vs stock) and for the long run.

## Commanded path (what the retest actually runs)

`driver --plan retest` → `plan_glimmer_retest` (driver.py:446) runs: setup, probe, the
**BF16 power-cap sweep** (`wattnot.proof.orchestrate --mode live`, driver.py:263-289),
the RUN3-failure blocks, **t2c-prefix** (`scripts/prefix_cache_ab.py`), and the
**combined block** (`scripts/prefix_cache_ab.py --combined --cap-on {BEST_CAP} --cap-off 700`,
driver.py:427-443). So A/B fairness lives in three places:

- **Sweep (stock vs cap):** `proof/orchestrate.py` → `proof/run.py` (host) → `proof/rig.py`
  (per-block metering) → `proof/session.py` (paired per-cap decision).
- **Prefix A/B + combined block (the headline):** `scripts/prefix_cache_ab.py` →
  `wattnot/context_saver/prefix_cache.py` (`run_ab`/`evaluate_ab`), reusing `rig.py`.
- **Tiers / FP8 / batch-KV gates:** `longrun/harness.py` (not the headline A/B, but checked).

## Round 1 findings (PASS / FAIL / NA, with evidence)

**1. Same prompts and seeds on both arms — PASS.**
`client.ask` pins `temperature 0, top_k -1, top_p 1.0, seed 0` and sends the identical prompt
(client.py:59-63); harness `Chat` matches (`temperature 0, seed 0, top_k -1`, harness.py:120-121).
Both arms replay the same workload file within one Rig: sweep = `proof-v1.jsonl` (hashed into the
receipt, orchestrate.py:1120); prefix/combined = `workloads/prefix-cache-v1.jsonl`
(prefix_cache_ab.py:144). One workload object is shared by both arms (rig.py:86).

**2. Alternating / paired rounds (ABBA or interleaved) — PASS.**
Sweep: `session.run_step` runs `reps × (stock, cap)` and reverses order on odd reps when
`counterbalance` is set (session.py:172-176); the sweep passes `--counterbalance`
(driver.py:283) and it reaches the Rig (run.py:230-232). Prefix/combined: `run_ab` rotates arm
order each rep (`(False, True) if rep % 2 else (True, False)`, prefix_cache.py:136).

**3. Identical warm-up per arm — PASS.**
`rig.block` runs the same untimed warm-up (count `self.warmup`) and timed warm-up (`warmup_s`)
at the arm's own cap *before* the energy window, for every block of both arms (rig.py:90-100).
`burn_in` is a single unmetered stock block once per process before the first measurement
(session.py:30-42) — a global cold-start settle applied before either arm is measured, not a
per-arm bias. Warm-up flags are forwarded identically to both arms (orchestrate.py:251-257).

**4. Stock arm really stock (700 W, prefix off, default batch/KV, BF16) + reset each stock round — PASS.**
Sweep stock arm = the GPU's own default limit (`stock_w = snap["idle"]["default_w"]`,
orchestrate.py:663); orchestrate never sets the stock limit, and `CapGuard` restores the saved
limit per step and on exit (run.py:225-226, 366-370). Every block re-applies its cap with a
readback check (rig.py:65-71, 81). Combined block: stock arm = `--cap-off 700` = `H100_STOCK_W`
(driver.py:441, 1068), prefix OFF via `--no-enable-prefix-caching`, and `glimmer_serve.sh`
serves BF16 with unchanged defaults (64 seqs, 0.90 mem-util, auto KV) for *both* arms — only the
prefix flag differs (glimmer_serve.sh:15-18,41-44; prefix_cache_ab.py:84-97). Batch/KV sweep is a
separate block (t2b-batch), not the combined block.

**5. Energy from the NVML total-energy counter over the same windows (warm-up excluded equally) — PASS.**
`rig.block` reads `nvml.total_energy_mj` immediately before (`e0`, after warm-up) and after
(`e1`) the metered pass only, in a try/finally; warm-up is outside the window (rig.py:121-130).
A non-advancing counter raises instead of recording a bogus number (rig.py:131-135;
harness.py:291-292). Both arms use the identical window construction.

**6. Quality grader uses only the final answer (not reasoning), identically on both arms — PASS.**
`client.ask` returns `strip_reasoning(content)` (client.py:34-36, 79), harness `Chat` the same
(harness.py:137); graders score that text only (grade.py:188-200, rig.py pass_rate). Same grader,
same final-answer text, both arms.

**7. Stats: CI and p-value on PAIRED differences — SPLIT: PASS (sweep) / FAIL → FIXED (prefix+combined).**
- Sweep (session.py): PASS. `summarize` builds `pairs = zip(stock, capped)`, per-pair
  `pct_change` deltas, `ci95` on the paired energy differences, and a Welch per-block test with a
  real p-value (session.py:64-100). Energy is accepted only when the paired CI is entirely below
  zero (session.py:80).
- Prefix A/B + **combined block (the retest headline)**: **FAIL (round 1).** `evaluate_ab`
  reported `j_per_correct_change_pct` / `ttft_change_pct` as a bare `mean(on)/mean(off)` point
  estimate (prefix_cache.py:90-92, 110-129) with **no CI and no p-value**, and it discarded the
  per-rep pairing that `run_ab` already produces (run_ab pairs off/on by rep index,
  prefix_cache.py:134-137). So the single most-quoted number of the retest — the combined
  saving % — carried no uncertainty and no significance, unlike the sweep. **FIXED** (see below).

**8. No cache leakage between arms — PASS.**
Combined/prefix A/B restarts vLLM per arm with the correct prefix flag inside every trial
(`serve(on)` in `trial`, prefix_cache_ab.py:104-127), so the prefix KV cache is flushed between
arms and the OFF arm has prefix caching disabled at the engine. Sweep: both arms hit the same
running server with a constant prefix setting, so there is no cross-arm prefix-cache asymmetry to
flush (orchestrate never changes server flags between arms, orchestrate.py:238-263).

**9. Per-token agent-waste A/B (direct vs proxy) — NA (not in the retest or long-run plan).**
No block in `plan_glimmer`, `plan_glimmer_test`, or `plan_glimmer_retest` invokes the agent-waste
bench (`wattnot.agentwaste.bench.*`); it is not imported outside its own package/tests
(driver.py:196,292,446 run only harness/vision/spoken/wonderchart/thinking_budget/prefix_cache_ab).
For the record, as a standalone bench it is largely fair (same tasks, temp 0, interleaved arms via
a seeded shuffle, symmetric failure handling, identical grader — runner.py:117-169), but it has
two weaknesses if ever promoted into a run: (a) the live `SparkSummarizer`'s own model tokens
would go uncounted on the saver arm (middleware.py:112-119 uses a separate client, not the bench's
`_Counting`), and (b) its summary reports only a point-estimate % change with no CI/p-value
(agentwaste/bench/summary.py:64). Out of scope for this review; flagged for the bench owner.

**10. Edge cases (failed / timeout / truncation counted fairly) — PASS.**
Failed requests: `client.ask` returns `finish="error"` with a placeholder answer, never dropped
(client.py:70-72); a failed `step` aborts the sweep loudly (orchestrate.py:491-492); any request
error in either arm blocks acceptance of a cap (session.py:89-93). Energy for failed/slow requests
is inside the counter window, so it is counted. Truncation: `finish != "stop"` is recorded per
request (`cut`, rig.py:144) and fed to the quality gate; the tiers summary reports the truncation
rate rather than silently scoring cut answers as misses (harness.py:197-199, 346-357). In the
combined block a truncation/error on the cap arm lowers that arm's pass rate and *raises* its
J/correct, i.e. it reduces the apparent saving (conservative). Round 2 sharpened the reverse
case — an error on the *stock/off* arm of the combined block would lower stock pass and so
*inflate* the headline `combined_saving_pct` (flattering the cap+prefix arm), the exact asymmetry
the sweep guards against (session.py:89-93) but `evaluate_ab` did not. **FIXED** (see below).

## Round 2 (independent re-review by a fresh subagent)

A fresh subagent re-reviewed the item-7 fix and hunted for anything round 1 missed:
- Fix verified correct: run_ab appends one off/one on row per rep in rep order, so the paired
  zip aligns the same rep (prefix_cache.py:192-195); `paired_change` math is sound (div-by-zero
  guarded, df = reps-1, two-sided p via `t_sf` bounded to [0,1], n<2 → all-None); the fix only
  ADDS reported stats and never changes `kept` or a recorded number. 25 relevant tests pass.
- Combined block is labelled as a two-lever saving, not prefix-only or cap-only (confirmed);
  no-passing-cap skips cleanly (no degenerate stock-vs-stock); long-run uses the same paired
  session.py machinery; prefix-arm warm-up is symmetric across arms.
- One standing fairness gap worth fixing: **item 10 / B5** — the combined headline is
  `j_per_correct_change_pct`, and a stock-arm error inflates it, yet `evaluate_ab` had no error
  gate and `trial` never propagated the error count. **FIXED.**

## Fixes applied (this branch)

1. **Item 7 — paired CI + p-value on the prefix/combined A/B (the retest headline).**
   `prefix_cache.paired_change()` pairs the per-rep arms and reports mean % change, the 95% CI
   half-width (Student t, reps-1 df, via `proof.stats.ci95`), whether the CI is entirely below
   zero, and a two-sided paired-t p-value (via `gates.noise.t_sf`). `evaluate_ab` now carries
   `headline.j_per_req_change_pct_paired`, `headline.j_per_correct_change_pct_paired`, and an
   `energy_saving_significant` flag (CI below zero AND p < 0.05) — reported, not a hard gate, so a
   thin 2-rep run states its uncertainty instead of asserting a saving the n cannot support.
   Files: `src/wattnot/context_saver/prefix_cache.py`. Tests: `tests/test_abfair_1031_fixes.py`.
2. **Item 10 / B5 — error gate on the combined/prefix A/B.** `scripts/prefix_cache_ab.py` now
   propagates each arm's `errors` (and `truncated`) into the per-rep row; `arm_summary` sums them;
   `evaluate_ab` blocks the gate on any request error in either arm and surfaces `gate.errors`
   per arm — the same rule as `proof.session.summarize`, so a stock-arm error can no longer
   silently flatter the saving. Files: `prefix_cache.py`, `scripts/prefix_cache_ab.py`.
3. **Cosmetic (B1) — honest `lever` label.** `annotate_combined` sets
   `lever = "power_cap + enable_prefix_caching"` so the raw combined JSON is not mis-read as a
   prefix-only result.

No measured/historic data files were changed; only computed stats and gating were added.

## Not fixed (out of scope / by design)

- **Item 9 (agent-waste A/B):** not in any retest/long-run plan — out of scope. Flagged for the
  bench owner: live `SparkSummarizer` overhead tokens would be uncounted on the saver arm
  (middleware.py:112-119), and its summary has no CI/p-value (agentwaste/bench/summary.py:64).
- **Tiers harness (g3/longrun/harness.py):** no timed warm-up and no counterbalance flag, but
  `run_block` runs ABBA so the cold/warm block cost is balanced across each pair; it is a
  secondary keep/reject tier gate, not the energy headline. Fair as used; left as-is.

## VERDICT

- **retest ready: YES.** Items 1-6, 8, 10 PASS; item 7 FAIL fixed (paired CI + p-value now on the
  combined headline); item 9 NA (not in plan). Same prompts/seeds, interleaved/ABBA rounds,
  symmetric warm-up outside the NVML energy window, genuinely-stock stock arm (700 W, prefix OFF,
  BF16, default batch/KV), final-answer-only grading, server restart flushing the prefix cache
  between arms, paired statistics on both the sweep and the combined block, and errors/truncation
  counted (and now gated) fairly. Caveat to report alongside the number, not a blocker: the
  retest runs the combined block at `--reps 2`, so the paired CI is wide; read
  `energy_saving_significant` and the CI, and prefer more reps where time allows.
- **long run ready: YES.** Uses the same paired `session.py` machinery with the `longrun`/`long`
  profiles (more reps, longer windows, counterbalance + burn-in on), and the same prefix A/B path.

Issues fixed: item 7 (paired CI/p-value on the prefix+combined headline); item 10/B5 (error gate
and per-arm error surfacing on the combined/prefix A/B). Cosmetic: combined `lever` label.
Commits: see `git log` on `review/ab-fairness-1031` (REVIEW-AB-1031 fix commits).
