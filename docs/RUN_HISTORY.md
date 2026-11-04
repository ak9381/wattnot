# Run history: every paid run, what it taught us, and what we changed

Every paid GPU or API run we did for WattNot is listed here, in order. Each entry keeps the
**original verdict exactly as the run recorded it**. We never edit a verdict after the fact.
When a later fix can be replayed for free on the data a run already saved, that result sits on its
own line marked **Re-scored after fix (REPLAY)**. It's not a new verdict, just a check of the fix
against real recorded numbers.

Labels: **MEASURED** = read from the GPU, billing or logs. **ESTIMATE** = computed (for example
wall minutes x list price). **REPLAY** = recomputed on CPU from saved per-item data, no GPU.
All times are IST (UTC+5:30). "not recorded" means we don't have the value, and we haven't guessed it.

Commit links point to `https://github.com/ak9381/wattnot/commit/<sha>`. Raw data lives on the
`data/modal-full-1101` and `data/a100-smoke-1101` branches.

## At a glance

| # | Run | When (IST) | Where | Cost | Original verdict | Main lesson | Fix |
|---|-----|------------|-------|------|------------------|-------------|-----|
| 1 | H100 retest | Oct 31, ~9:35 AM to 3:45 PM | H100 VM | ~₹1,571 ESTIMATE | First sweep crashed; rerun: 375/350/325 W passed, 300 W failed p99 | NaN in receipt stats crashed the sweep | [5ab7e04](https://github.com/ak9381/wattnot/commit/5ab7e04) |
| 2 | A100 smoke 4 (`smoke-ui1645`) | Nov 1, 10:16 to 10:39 PM | Modal A100 | $1.24 MEASURED | held; 4 blocks failed | Agent-saver block too long for a smoke | [be1e7cb](https://github.com/ak9381/wattnot/commit/be1e7cb) |
| 3 | A100 smoke 5 (`smoke-ui1728`) | Nov 1, 10:59 to 11:18 PM | Modal A100 | $0.99 MEASURED | held; 0 blocks failed | Certificate was still a mock | [72b2ea6](https://github.com/ak9381/wattnot/commit/72b2ea6) |
| 4 | Baseten smoke `20261101-180328-a534` | Nov 1, ~11:33 PM | Baseten API | $0.21 MEASURED + $0.01 Meta | rejected | Low-effort runaways, weak hearing clips, gauge misread | [3bf0ade](https://github.com/ak9381/wattnot/commit/3bf0ade) |
| 5 | Baseten smoke `20261101-191931-6df1` | Nov 2, ~12:49 AM | Baseten API | $0.30 MEASURED + $0.06 Meta | rejected | Only the eye gate still failed (gauge) | [71d60d7](https://github.com/ak9381/wattnot/commit/71d60d7) |
| 6 | Modal r04c | Nov 2, 5:53 to 7:40 AM | Modal A100 | $4.87 MEASURED | **approved**; 2 blocks failed | Container had no Meta key; LangChain saver arm 11/20 | [fd1726d](https://github.com/ak9381/wattnot/commit/fd1726d), [d9f83e9](https://github.com/ak9381/wattnot/commit/d9f83e9) |
| 7 | Modal r04d | Nov 2, 8:18 to 8:33 AM | Modal A100 | $0.70 ESTIMATE | no verdict (crashed at start) | Secret added conditionally, missing remotely | [a653d5a](https://github.com/ak9381/wattnot/commit/a653d5a) |
| 8 | Modal r04e | Nov 2, 9:13 to 11:31 AM | Modal A100 | $6.02 MEASURED + $0.39 Meta | rejected | Context saver broke long agent chats | [458215f](https://github.com/ak9381/wattnot/commit/458215f), [1588455](https://github.com/ak9381/wattnot/commit/1588455), [ed1e21b](https://github.com/ak9381/wattnot/commit/ed1e21b) |
| 9 | r04e LangChain-only, try 1 | Nov 2, 12:47 to ~1:18 PM | Modal A100 | $1.35 MEASURED | no verdict (Modal cut the app) | Local client lost heartbeats | [bed7e37](https://github.com/ak9381/wattnot/commit/bed7e37) |
| 10 | r04e LangChain-only, try 2 | Nov 2, 1:41 to 2:16 PM | Modal A100 | $1.44 MEASURED | 20/20 both arms; saver arm +42% energy | Final-check made extra calls on empty `done` replies | [7846cf1](https://github.com/ak9381/wattnot/commit/7846cf1) |
| 11 | Modal r04f (round 1, try 3) | Nov 2, 2:44 to 4:26 PM | Modal A100 | $4.42 MEASURED | **rejected** on p99 only | Step-ups count both attempts' time | [dc143e6](https://github.com/ak9381/wattnot/commit/dc143e6), [1a2c7f1](https://github.com/ak9381/wattnot/commit/1a2c7f1) (not merged yet) |

The short version: energy per correct answer on the full Modal round went from **-63.6%** (r04c)
to **-23.9%** (r04e, when the saver broke agent tasks) to **-67.6%** (r04f) (MEASURED).
Every failure in between has a named cause and a commit.

---

## 1. H100 retest (Oct 31)

- **When:** launched about 9:35 AM IST, finished and VM deleted about 3:45 PM IST, Oct 31.
- **Commit:** fixes merged before the rerun at [90cdb3e](https://github.com/ak9381/wattnot/commit/90cdb3e); public benchmark check merged at [b8428dd](https://github.com/ak9381/wattnot/commit/b8428dd).
- **Cost:** about ₹1,571 ESTIMATE for the VM.
- **Original verdict:** the first sweep tested only 375 W and then crashed on a NaN in the receipt (about 1:33 PM IST). On the rerun, 375, 350 and 325 W passed; 300 W failed its p99 check (+18%).
- **Key numbers (MEASURED, 2 paired reps each):**
  - Energy per request vs stock 700 W: 375 W -26.1%, 350 W -31.4%, 325 W -34.3%, 300 W -31.8%.
  - Combined 325 W + prefix caching vs stock: -44.1% J per correct answer, TTFT -84.4%, p99 -12.3%, pass rate 100% both; gate passed.
  - Public sets at 325 W (prefix off): GSM8K 99.2% -> 100%, J/correct 425.5 -> 314.9; HumanEval 92.5% both, J/correct 982.3 -> 705.5. GSM8K p99 got worse (32.0 s -> 47.8 s).
- **What failed and why:** a NaN in the receipt statistics crashed the sweep after the first cap (375 W).
- **Fix:** [5ab7e04](https://github.com/ak9381/wattnot/commit/5ab7e04) (measure every grid cap, NaN stats saved as null), merged in [90cdb3e](https://github.com/ak9381/wattnot/commit/90cdb3e).
- **What the next run showed:** the same-VM rerun finished the whole grid with no crash.
- **Re-scored after fix (REPLAY):** not replayable. The 300 W p99 miss is a real hardware result. The eye, spoken, FP8, batching and prefix blocks skipped on that VM were not rerun on H100.

## 2. A100 smoke 4 (`smoke-smoke-ui1645`)

- **When:** Nov 1, 10:16 to 10:39 PM IST.
- **Commit:** not recorded in the run; main at launch was [1353fc6](https://github.com/ak9381/wattnot/commit/1353fc6).
- **Cost:** $0.99 ESTIMATE, $1.24 MEASURED (Modal billing).
- **Original verdict:** approval **held** (Spark grading was off for smokes). Failed blocks: `context_saver`, `waste_guard`, `eye_check`, `hearing_check`.
- **Key numbers (MEASURED, 3 items per arm):** accuracy 3/3 both; J/correct 4,413.0 -> 3,393.6 (-23.1%); p99 29.6 s -> 21.2 s.
- **What failed and why:** the agent-saver block hit its timeout (rc 124), and LangChain, eye and hearing were then skipped because the container ran out of time.
- **Fix:** [be1e7cb](https://github.com/ak9381/wattnot/commit/be1e7cb), a shorter smoke agent block (1 rep, 4 cases, 240 s cap), merged in [0702a3d](https://github.com/ak9381/wattnot/commit/0702a3d). Full rounds unchanged.
- **What the next run showed:** smoke 5 ran every block with no failures.
- **Re-scored after fix (REPLAY):** not replayable (the skipped blocks never ran).

## 3. A100 smoke 5 (`smoke-smoke-ui1728`)

- **When:** Nov 1, 10:59 to 11:18 PM IST.
- **Commit:** not recorded in the run; main at launch was [0702a3d](https://github.com/ak9381/wattnot/commit/0702a3d).
- **Cost:** $0.82 ESTIMATE, $0.99 MEASURED.
- **Original verdict:** approval **held**, no failed blocks.
- **Key numbers (MEASURED, 3 items per arm):** accuracy 3/3 both; J/correct 3,798.1 -> 3,422.6 (-9.9%); p99 22.0 s -> 20.2 s.
- **What failed and why:** no block failed. The flow step still exited non-zero because the certificate was a mock and refused a NOISE grade.
- **Fix:** [72b2ea6](https://github.com/ak9381/wattnot/commit/72b2ea6), a certificate built from the measured receipt (NOISE declines cleanly), merged in [edcf170](https://github.com/ak9381/wattnot/commit/edcf170).
- **What the next run showed:** the full rounds (r04 onward) ran with the live certificate.
- **Re-scored after fix (REPLAY):** not needed (nothing failed on latency or accuracy).

## 4. Baseten smoke `20261101-180328-a534`

- **When:** Nov 1, about 11:33 to 11:39 PM IST.
- **Commit:** not recorded in the run; main at launch was [3cc8b5b](https://github.com/ak9381/wattnot/commit/3cc8b5b) (fully live Baseten smoke).
- **Cost:** Baseten $0.2119 MEASURED (billing delta matched token price, 0.0% diff), Meta $0.0112 MEASURED.
- **Original verdict:** **rejected**.
- **Key numbers (6 items per arm):** accuracy 6/6 both (MEASURED); p99 6.5 s -> 10.3 s, +57% (MEASURED); J/correct 558.7 -> 669.9 (ESTIMATE, there's no GPU meter on an API). Eye gate 9/10 (gauge misread). Hearing gate misheard 12/20.
- **What failed and why:** low-effort answers could run on too long before stepping up; the spoken test clips were hard to transcribe; the gauge needle was ambiguous.
- **Fix:** [3bf0ade](https://github.com/ak9381/wattnot/commit/3bf0ade) (cheaper low-effort runaway cap with step-up reasons, neural TTS hearing clips, clearer gauge, bigger smoke), merged in [2b1cf05](https://github.com/ak9381/wattnot/commit/2b1cf05).
- **What the next run showed:** in smoke `6df1`, p99, energy and hearing all passed.
- **Re-scored after fix (REPLAY):** not replayable (the fixes change what the model is asked, so they need a live call).

## 5. Baseten smoke `20261101-191931-6df1`

- **When:** Nov 2, about 12:49 to 12:56 AM IST.
- **Commit:** not recorded in the run; main at launch was [edcf170](https://github.com/ak9381/wattnot/commit/edcf170).
- **Cost:** Baseten $0.3042 MEASURED (billing matched, 0.0% diff), Meta $0.0551 MEASURED.
- **Original verdict:** **rejected**.
- **Key numbers (30 items per arm):** accuracy 27/30 -> 30/30 (MEASURED); p99 14.4 s -> 7.3 s (MEASURED); J/correct 967.0 -> 327.5 (ESTIMATE). Hearing passed. Eye 9/10, gauge misread again.
- **What failed and why:** only the eye gate, on the same gauge image.
- **Fix:** gauge redrawn with clear labels and ticks: [71d60d7](https://github.com/ak9381/wattnot/commit/71d60d7), then [decb3f4](https://github.com/ak9381/wattnot/commit/decb3f4) (labels every 20, strict check restored).
- **What the next run showed:** the eye check scored 10/10 in r04f (MEASURED, Modal). The Baseten public round hasn't run yet, so the Baseten eye fix is not yet confirmed live.
- **Re-scored after fix (REPLAY):** not replayable (a new image needs a new model call).

## 6. Modal r04c (full round)

- **When:** Nov 2, 5:53 to 7:40 AM IST.
- **Commit:** [1c533dd](https://github.com/ak9381/wattnot/commit/1c533dd).
- **Cost:** $4.46 ESTIMATE, $4.87 MEASURED.
- **Original verdict:** **approved**. Failed blocks: `waste_guard` (LangChain) and `hearing_check`.
- **Key numbers (MEASURED, 192 items per arm):** J/correct 2,254.7 -> 821.6 (-63.6%); accuracy 0.958 -> 0.990; p99 101.8 s -> 84.4 s; agent-waste block passed in 538 s; complex agent tasks 12/12.
- **What failed and why:**
  - LangChain saver arm got 11/20 against 19/20 without (MEASURED). The router answered 70/71 calls with thinking off and the saver saved 0 tokens.
  - Hearing got HTTP 401 from Meta ASR because the container had no `META_API_KEY`.
- **Fix:** [fd1726d](https://github.com/ak9381/wattnot/commit/fd1726d) (Meta key passed as a Modal Secret, preflight key check), then [d9f83e9](https://github.com/ak9381/wattnot/commit/d9f83e9) (final-answer router: tool turns not judged, one thinking step-up per run), merged in [3a45f27](https://github.com/ak9381/wattnot/commit/3a45f27).
- **What the next run showed:** r04d crashed at startup (see below). In r04e, hearing got its key, but LangChain still failed.
- **Re-scored after fix (REPLAY):** not replayable for the failed blocks. The latency guard and fallback (see r04f) leave this run's latency at pass (p99 84.4 s, or 73.7 s with the guard, REPLAY).

## 7. Modal r04d

- **When:** Nov 2, 8:18 to 8:33 AM IST.
- **Commit:** [3a45f27](https://github.com/ak9381/wattnot/commit/3a45f27).
- **Cost:** $0.70 ESTIMATE (16.7 min).
- **Original verdict:** none. The GPU container crash-looped at startup and was stopped by hand.
- **Key numbers:** none; no item ran.
- **What failed and why:** Modal `ExecutionError` (3 dependencies vs 4 object ids). The Meta secret was added conditionally and was missing on the remote side.
- **Fix:** [a653d5a](https://github.com/ak9381/wattnot/commit/a653d5a) (named secret `wattnot-meta`, CPU startup probe in preflight), merged in [175509e](https://github.com/ak9381/wattnot/commit/175509e).
- **What the next run showed:** r04e started cleanly and ran to the end.
- **Re-scored after fix (REPLAY):** not replayable (no data).

## 8. Modal r04e (full round)

- **When:** Nov 2, 9:13 to 11:31 AM IST.
- **Commit:** [175509e](https://github.com/ak9381/wattnot/commit/175509e).
- **Cost:** Modal $5.78 ESTIMATE, $6.02 MEASURED; Meta $0.39.
- **Original verdict:** **rejected** (accuracy and p99). LangChain block failed.
- **Key numbers (MEASURED):** J/correct 2,437.5 -> 1,855.5 (-23.9%); accuracy 0.974 -> 0.943 (-3.13 pts); p99 77.6 s -> 498.7 s; complex agent tasks 12/12 (r04c) -> 4/12; LangChain saver arm 5/20 vs 19/20; FP8 rejected (22/24, accuracy drop 4.16 pts).
- **FP8 note (superseded):** the FP8 number above (22/24, -4.16 pts, MEASURED) is from this earlier Modal A100 run. It is **superseded by the Nov 4 H100 refusal**, which is the FP8 story of record: on the H100, one-lever FP8 cut energy -42.5% but matched only 15/16 answers, so the gate refused it and rolled back to BF16 (MEASURED, `replays/h100-max-1103/`). This entry keeps its original Modal number unedited for history.
- **What failed and why:** the context saver summarised away key facts in long multi-step agent chats, so agents looped and answered wrong. That caused both the accuracy drop and the huge p99.
- **Fix:**
  - [458215f](https://github.com/ak9381/wattnot/commit/458215f) pins the task in the saver, adds a per-call chunk store and finalises done-only replies.
  - [583fdd1](https://github.com/ak9381/wattnot/commit/583fdd1) keeps short tool turns verbatim.
  - [1588455](https://github.com/ak9381/wattnot/commit/1588455) adds the smart saver, which keeps key facts verbatim.
  - [ed1e21b](https://github.com/ak9381/wattnot/commit/ed1e21b) makes the saver compress only chats of 500+ tokens.
  - Merges: [6101971](https://github.com/ak9381/wattnot/commit/6101971), [e3a0415](https://github.com/ak9381/wattnot/commit/e3a0415), [7dcbf6f](https://github.com/ak9381/wattnot/commit/7dcbf6f), [38f6527](https://github.com/ak9381/wattnot/commit/38f6527).
  - Before r04f, a CPU replay of complex-00..10 with the new saver scored 11/11 in all 4 agent styles (MOCK).
- **What the next run showed:** in r04f, accuracy recovered to 0.984, p99 fell from 498.7 s to 104.6 s, and J/correct savings went back to -67.6% (MEASURED).
- **Re-scored after fix (REPLAY):** the latency guard and fallback alone don't rescue this run. The verdict is still **fail**: p99 128.0 s with the guard vs 77.6 s without WattNot, and p95 302.4 s, so the fallback doesn't apply (REPLAY). The saver fix itself is not replayable on this run's GPU data.

## 9. r04e LangChain-only run, try 1 (`r04e-langchain-20261102T071628Z`)

- **When:** Nov 2, 12:47 PM IST; Modal stopped the app around 1:18 PM IST.
- **Commit:** main [7dcbf6f](https://github.com/ak9381/wattnot/commit/7dcbf6f).
- **Cost:** $1.35 MEASURED (billing).
- **Original verdict:** none. The run was cut about 17 minutes into LangChain, and no per-question results were saved.
- **What failed and why:** Modal stopped the app with "local client disconnected" (missed heartbeats). Weights also took 753.5 s to load.
- **Fix:** [bed7e37](https://github.com/ak9381/wattnot/commit/bed7e37) and [fdc252e](https://github.com/ak9381/wattnot/commit/fdc252e) (`modal run --detach` with a wait for app stop, per-item lines synced every 30 s, weight prefetch, 90 min block cap), merged in [36a971a](https://github.com/ak9381/wattnot/commit/36a971a).
- **What the next run showed:** try 2 ran to completion, and weights loaded in 21.4 s (MEASURED).
- **Re-scored after fix (REPLAY):** not replayable (no results saved).

## 10. r04e LangChain-only run, try 2 (`r04e-langchain-20261102T081001Z`)

- **When:** Nov 2, 1:41 to 2:16 PM IST.
- **Commit:** [36a971a](https://github.com/ak9381/wattnot/commit/36a971a).
- **Cost:** $1.44 MEASURED.
- **Original verdict:** block passed, 20/20 saver off and 20/20 saver on (MEASURED).
- **Key numbers (MEASURED):** GPU energy 271.4 kJ (off) vs 385.1 kJ (on), +42%; J/correct 13,568 vs 19,255; wall 794 s vs 1,113 s.
- **What failed and why:** quality was fixed, but the saver-on side used more energy. The saver itself saved 0 tokens; LangChain calls average about 141 tokens (MEASURED). The extra cost came from the final check: on 14 of 21 final answers the agent ended with an empty `done`, so the check made an extra call, on the thinking tier. Separately, the local copy-back crashed on a 0-byte `_incoming` file (data recovered from the volume).
- **Fix:**
  - [7846cf1](https://github.com/ak9381/wattnot/commit/7846cf1) salvages answers from `done` replies, finalises on the cheapest tier first and asks `done` for the answer, merged in [95b4431](https://github.com/ak9381/wattnot/commit/95b4431).
  - Copy-back fix [5d6ab59](https://github.com/ak9381/wattnot/commit/5d6ab59).
- **What the next run showed:** in r04f's LangChain block, before the final-check tune was merged, the saver-on side was 20/20 vs 19/20 and used +12.7% energy (MEASURED). A LangChain-only rerun with the tune was running when this page was written; its result is not recorded here yet.
- **Re-scored after fix (REPLAY):** the CPU replay of this run's call pattern with the tune cut tokens from 13,167 to 5,367-3,318 with 20/20 correct. That replay is **MOCK** (simulated agent replies), not a re-score of GPU data.

## 11. Modal r04f (round 1, try 3)

- **When:** Nov 2, 2:44 PM IST (GPU start) to 4:26 PM IST (GPU done); final verdict 4:34 PM IST.
- **Commit:** [38f6527](https://github.com/ak9381/wattnot/commit/38f6527).
- **Cost:** $4.28 ESTIMATE, $4.42 MEASURED. Stage total about $31.13 of the $60 cap (ESTIMATE).
- **Original verdict:** **rejected**, failed check `p99` only.
- **Key numbers (MEASURED, 192 items per arm):**
  - J/correct 2,017.3 -> 653.8 (-67.6%); accuracy 0.974 -> 0.984; Spark agreement 0.953.
  - p99 88.4 s -> 104.6 s (+18%).
  - Blocks: think 376 s, agent-waste 576 s, LangChain 19/20 (off) vs 20/20 (on).
  - Eye 10/10 and hearing 20/20 on BF16 and FP8. FP8 J/correct 2,038.3 vs BF16 3,596.8, same accuracy.
- **What failed and why:** item p99 is the 3rd-slowest of 192 items. Two items stepped up from low effort to thinking, and both attempts' time counted: HumanEval/32 (39 s low + 114 s thinking = 153.1 s) and complex-11 (73 s low + 80 s thinking). Per single call, WattNot's p99 was faster (38.7 s vs 58.1 s, MEASURED).
- **Fix (on branch `fix/stepup-latency-guard`, not merged yet):**
  - [dc143e6](https://github.com/ak9381/wattnot/commit/dc143e6) is the step-up latency guard: if the low attempt already took over 30 s, keep its answer.
  - [1a2c7f1](https://github.com/ak9381/wattnot/commit/1a2c7f1) is the latency fallback, fixed before round 2: "pass with a latency note" when p95 passes and at most 2 slow items cause the p99 miss.
- **What the next run showed:** pending. Round 2 (public dataset) runs with both rules set beforehand.
- **Re-scored after fix (REPLAY):** these lines were added after this round's numbers were known, so they don't change the original verdict.
  - Fallback rule only: **pass with latency note** (REPLAY). p95 61.96 s vs 65.83 s passes; dropping HumanEval/32 alone gives p99 93.6 s, within +10% of 88.4 s.
  - Guard + fallback: **pass** (REPLAY). p99 82.3 s vs 88.4 s (-6.9%), p95 59.1 s. Accuracy with the guard is 188/192 (0.979), from the guard investigation (REPLAY). The saved answer files don't store which low answers were correct, so this page doesn't recompute it.

---

## Other paid runs (short)

These were also paid runs. They are not written up in full here.

| Run | Cost (stage ledger) | Outcome |
|-----|---------------------|---------|
| A100 smokes `smoke-r04`, `smoke-r05`, `ui1426`, `ui1554`, `ui1617` (Nov 1) | $0.38, $0.30, $0.89, $0.77, $0.64 (incl. true-ups) | Early smokes; `ui1617` lost its client connection, which led to the incremental-save runner |
| r04 aborted at launch (Nov 2, 1:00 AM) | $0.125 ESTIMATE | Recorder failed to start |
| r04 try 1 (Nov 2, 1:10 to 2:53 AM) | $4.08 MEASURED | J/correct -68.3%; agent-waste timeout, eye 63 vs 60, Muse Code KeyError, optuna missing |
| r04 rerun, stopped (Nov 2, ~2:59 to 3:43 AM) | $1.84 ESTIMATE | Stopped by decision; not rerun |
| r04b (Nov 2, 4:16 to 4:28 AM) | $0.79 ESTIMATE | Stopped from outside by another script; led to the owner lock (`d935988`) |

## How to check this page

- Verdicts and numbers: `data/modal-full-1101/<run>/round.json` (`approval`, `results`, `spend`, `failed`) on the data branches.
- Baseten smokes: `data/baseten-e2e-smoke-1101/<job>/r01/features/approval.json` and `billing_crosscheck.json`.
- REPLAY lines: `src/wattnot/e2e/latency_guard.py` and `tests/test_stepup_latency_guard.py` on `fix/stepup-latency-guard`, run on the saved `raw/with.answers.jsonl`, `raw/without.answers.jsonl` and `raw/with.calls.jsonl`.
