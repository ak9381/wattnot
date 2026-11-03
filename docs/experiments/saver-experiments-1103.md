# Context saver and output-budget experiments (2026-11-03)

This is every attempt we made to make the LangChain saver arm save energy on Muse Glimmer 30B by
asking for less thinking or less output, plus the one thing that did work (the H100 thinking
budget). Nothing here was patched after the run. Every number has a label.

Setup for all Modal LangChain runs: our own 20 math items, a real LangChain agent with tools,
all traffic through the WattNot agent-waste proxy, saver OFF arm then saver ON arm, GPU energy
from the GPU's own counter. Score metric: joules per correct answer (lower is better).

## Summary

| Run | What changed | Saver ON correct | OFF correct | ON J/correct | OFF J/correct | Cost |
|---|---|---|---|---|---|---|
| v1 | rerun of the saver as on sim-main | 20/20 | 19/20 | 14,687 | 12,311 | n/a here |
| v2 | 5770e9b: per-question tokens + final-check thinking cap | 19/20 | 20/20 | 16,091 | 14,036 | $1.39 ESTIMATE |
| smoke | does `enable_thinking=false` work on Glimmer? | - | - | - | - | $0.49 ESTIMATE |
| v3 | 448-token output budget + step-up, temp 0, seed 1234 | 16/20 | 19/20 | 17,322 | 15,195 | $1.25 ESTIMATE |
| v4 | v3 with the step-up bug fixed | 20/20 | 19/20 | 17,089 | 13,562 | $1.39 ESTIMATE |

Correct counts and joules are MEASURED. Modal total across all these and earlier runs:
$9.86 ESTIMATE.

## v1: plain rerun (r04-langchain-20261103T062925Z)

- ON 20/20, 293,746 J, 14,687 J per correct. OFF 19/20, 233,910 J, 12,311 J per correct (MEASURED).
- Wall time: ON 982 s, OFF 784 s (MEASURED).
- Verdict: inconclusive on energy. One question ran 256 s with the saver against 77 s without
  (MEASURED, from the run log; per-item times are not in the result file), and that one item
  explains much of the gap. Quality was fine.
- The context saver saved 0 tokens and the final check never stepped up: on 20 short math
  questions there is no long context to trim.

## v2: per-question tokens and a final-check thinking cap (r04-langchain-20261103T103719Z)

- New code 5770e9b: per-question token totals in the summary and a thinking cap on the final check.
- ON 19/20, 305,732 J, 16,091 J per correct. OFF 20/20, 280,711 J, 14,036 J per correct (MEASURED).
- None of the saver features fired: 0 step-ups, 0 tokens saved. So the ON arm was in effect a
  second OFF arm, and the gap is run-to-run noise plus proxy overhead, not a saver effect.
- Cost $1.39 ESTIMATE.

## Thinking-off smoke test

- Question: does Glimmer honour `chat_template_kwargs.enable_thinking=false` on vLLM?
- Answer: no. `enable_thinking=True` and `=False` gave byte-identical outputs and token counts
  (MEASURED). The chat template has no such flag, so vLLM just ignores it.
- Adding `/no_think` to the prompt gave about 5% fewer tokens (MEASURED), but only because the
  prompt text changed, not because thinking turned off. The reasoning block was still there.
- Cost $0.49 ESTIMATE.
- Files: `/workspace/hackathon/video/thinking-off-smoke-1103.json` and
  `/workspace/hackathon/video/thinking-smoke/` (box paths, not in the repo).

This is why the dashboard and docs no longer say the saver "turns thinking off" on Glimmer.

## v3: 448-token output budget plus step-up (r04-langchain-20261103T121412Z)

- Idea: since thinking cannot be turned off, cap `max_tokens` at 448 and step up to the full
  budget when the final check says the answer is shaky. Temperature 0, seed 1234 on both arms.
- ON 16/20, 277,155 J, 17,322 J per correct. OFF 19/20, 288,698 J, 15,195 J per correct (MEASURED).
- Total energy was about 4% lower with the saver, but per correct answer it was about 14% worse.
- Bug: the step-up fired (4 times, MEASURED) but its output never replaced the cut-off answer, so
  three questions ended empty or cut mid-sentence.
- Cost $1.25 ESTIMATE.

## v4: fixed step-up (r04-langchain-20261103T131342Z)

- Branch `feat/glimmer-out-budget-1103` at `dc5b6fa`.
- ON 20/20, 341,772 J, 17,089 J per correct. OFF 19/20, 257,684 J, 13,562 J per correct (MEASURED).
  That is 26% worse per correct answer.
- 11 of 11 step-ups worked (MEASURED), so quality was fixed.
- Why energy got worse: each retry pays twice, once for the short attempt that gets cut off and
  again for the full one. With 11 retries on 20 questions, the short attempts are pure waste.
  Glimmer needs most of its thinking on these items, so a tight cap mostly just forces a retry.
- Cost $1.39 ESTIMATE.

## What did work: H100 thinking budget (pass 2, p2-think)

Mechanism: stop-and-force-answer on the reasoning stream, budget picked per difficulty tier,
gated on J per correct with zero quality drop allowed. n = 4 per cell, all 100% correct (MEASURED).

| Tier | Budgets tried | J/correct at 1.0x | Chosen | J/correct chosen | Saving |
|---|---|---|---|---|---|
| easy | 0.25x, 0.5x, 1.0x | 787 | 0.25x | 299 | -62% |
| medium | 0.5x, 1.0x, 1.5x | 2,439 | 0.5x | 2,438 | about 0% |
| hard | 0.5x, 1.0x, 2.0x | 2,897 | 0.5x | 2,897 | about 0% |
| complex | 1.0x, 1.5x | 16,509 | no change | 16,509 | none |

All MEASURED. The saving is real on easy prompts only, and the sample is small (4 per cell).
Medium and hard did not change because Glimmer finished thinking under the budget anyway.

## Decision

2026-11-03, 7:36 PM IST: drop the output-budget tier and keep sim-main as it is. No more Modal
spend on this. The power cap stays as WattNot's main saving; the per-tier thinking budget stays
for easy prompts. The step-up work is kept on `feat/glimmer-out-budget-1103` for reference.

Lessons:
- Check that a switch actually does something before building on it (thinking-off did not).
- A retry-based saver only pays if most first tries succeed. Here 11 of 20 needed a retry.
- On 20 short items, one slow question can swing the whole energy result. Treat single
  runs like v1 as noise until repeated.

## Result files (box paths)

- v1: `/tmp/wn-modal-lc/data/modal-full-1101/attachments/r04/r04-langchain-20261103T062925Z/langchain/langchain_pass.json`
- v2: `/tmp/wn-modal-lc/data/modal-full-1101/attachments/r04/r04-langchain-20261103T103719Z/langchain/langchain_pass.json`
- v3: `/workspace/wn-outbudget/data/modal-full-1101/attachments/r04/r04-langchain-20261103T121412Z/langchain/langchain_pass.json`
- v4: `/workspace/wn-outbudget/data/modal-full-1101/attachments/r04/r04-langchain-20261103T131342Z/langchain/langchain_pass.json`
- Smoke test: `/workspace/hackathon/video/thinking-off-smoke-1103.json`, `/workspace/hackathon/video/thinking-smoke/`
- H100 p2-think: `/workspace/hackathon/e2e-glimmer-test/live-max-1103/pulled/run2/results/p2-think/thinking_policy.json`
- Branch: `feat/glimmer-out-budget-1103` (dc5b6fa)
