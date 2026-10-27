# Adaptive top-k (optional, off by default)

Adaptive top-k is a **known technique**. WattNot did not invent it and does not claim it did.
This page says what the optional H100 stage does, how it measures, and what the number does
and does not mean.

## The idea

A mixture-of-experts (MoE) layer sends each token to its top-k experts (Granite 3.1
3B-A800M: 8 of 40). Some tokens are easy: a few experts hold almost all of the router weight.
Adaptive top-k keeps experts, biggest weight first, until they cover a chosen share of the
weight (`mass`, for example 0.9) and skips the rest. Fewer experts run for that token, so there
is less expert compute, at some cost in agreement with the normal model.

```
weights (sorted):   0.50  0.30  0.15  0.03  0.01  0.01     mass 0.9
running total:      0.50  0.80  0.95   ...                  keep 3, skip 3
```

The first expert is always kept, and the kept weights are renormalised to sum to 1.

## Prior art (read these, not us)

- Lu et al. 2024, *Not All Experts are Equal: Efficient Expert Pruning and Skipping for MoE
  LLMs*, arXiv:2402.14800. Skips a second expert when its weight is small next to the first;
  they report 1.2 to 1.3x speed-ups.
- Huang et al. 2024, *Harder Tasks Need More Experts: Dynamic Routing in MoE Models*,
  arXiv:2403.07652 (ACL 2024). Top-p style routing: the number of experts follows a
  cumulative-probability threshold. This is the closest match to what we run.
- Yue et al. 2025, *Ada-K Routing*, ICLR 2025 (OpenReview 9CqkpQExe2). Learns how many
  experts each token gets. (An earlier arXiv version, 2410.10456, was withdrawn, so cite
  the ICLR entry.)

## What WattNot adds

Only the measurement and the paperwork: joules per token from the NVML energy counter (not a
model), p99 latency, agreement with normal top-k under the same load, a signed receipt, and an
approval step before anything is switched on. Nothing about the routing rule itself is ours.

## Why a transformers hook and not vLLM

As far as we checked, vLLM's fused MoE layer takes one fixed `top_k` for every token and has no
per-token adaptive option (an upstream request for variable expert counts was closed without
adding it). So this stage runs the model with Hugging Face transformers and a small hook,
`src/wattnot/moe/hf_backend.py`:

- the router wrapper keeps the experts that cover `mass` and points the rest at a sentinel id;
- an eager expert loop (the same loop in both arms) skips the sentinel, so dropped experts cost
  nothing.

Both arms (normal and adaptive) go through this identical code, and only the threshold differs.
That keeps the comparison fair, but it also means **a saving here is not a statement about
vLLM's fused kernels**, which are faster than an eager loop and may not gain the same. Treat the
result as "does adaptive top-k save energy on this model on this card", not as a serving number.

## Model choice

Default: `ibm-granite/granite-3.1-3b-a800m-instruct` (Apache 2.0, no licence click). 3.3B
parameters, 40 experts per layer, top-8, about 7 GB in bf16, so it fits one H100 80GB with
room to spare. It is also the model of the earlier CPU test, so the numbers line up with it.
That CPU test (MEASURED on CPU, not on a GPU) cut active experts per token from 8 to 6.8 at
mass 0.9, about 15% less expert compute, for about 1.4 points of token agreement.
Energy was never measured there; that is the gap this stage fills.

## Choosing the mass (default 0.95)

RUN2 used mass 0.9 on the H100 and got 94.5% token agreement (484 of 512 positions), just under
the 95% gate, so the stage filed no proposal. Agreement needs no GPU, so the threshold was tuned
on a CPU: `scripts/tune_adaptive_mass.py` (numbers in `data/adaptive-tune/tune.json`; pass
`--report data/adaptive-tune/tune.json` to print the table again without a model).

Three prompt sets, bf16 weights, the same hook code as the GPU run. All agreement and expert
numbers below are MEASURED on the CPU. The saving column is an ESTIMATE.

| mass | worst-set agreement | lower 95% bound | experts active (of 8) | expert cut | J/token saving (ESTIMATE) |
|---|---|---|---|---|---|
| 0.90 | 93.3% | 90.7 | 6.79 | 15.1% | -6.7% |
| 0.92 | 95.2% | 93.3 | 7.15 | 10.7% | -4.7% |
| 0.94 | 95.4% | 93.8 | 7.58 | 5.3% | -2.4% |
| **0.95** | **96.7%** | **95.5** | **7.73** | **3.3%** | **-1.5%** |
| 0.96 | 97.5% | 96.1 | 7.84 | 2.0% | -0.9% |

The rule: the smallest mass where every set clears 96% (gate plus a one point margin) and its
lower 95% bound is still at least 95%. That is 0.95. Lower masses pass on some sets and fail on
another (0.92 and 0.94 both dip under the margin on the held-out prompts), which is how RUN2
landed at 94.5%.

Two honest caveats. First, the CPU is not the H100: at mass 0.9 on RUN2's own 16 prompts the CPU
reads 94.9% against the H100's 94.5% (2 of 512 positions apart), so expect a few tenths of a
point either way. Second, the saving is scaled from RUN2's one measured point (15% fewer expert
slots gave -6.6% J/token), so it is an ESTIMATE of about -1.5% J/token at 0.95. That is small
next to run-to-run noise, so the energy gate (lower J/token than normal) can fail on a noisy
run. Measure it on the GPU before quoting it.

The agreement check on the GPU now uses 32 prompts of 48 tokens (1,536 positions, was 512) so
the 95% gate is read with a tighter error bar. The first 16 prompts are the ones RUN2 used.
Override the mass with `--adaptive-mass`.

OLMoE-1B-7B (64 experts, top-8) is the usual alternative. The hook is only wired for the
Granite layout today, and any other architecture stops with a clear error before measuring.
`--adaptive-model REPO` accepts another Granite MoE repo.

## How it measures

- Same load for both arms: the same 32 prompts in the same order (`workloads/replay-v1.jsonl`
  then `data/flex/load-prompts.jsonl`), batches of 8, greedy decoding, exactly 64 new tokens
  (no early stop, so both arms do the same amount of work).
- Arms alternate (off, on, on, off, ...) for up to 5 repeats each, after a discarded warm-up, so
  drift in clocks or temperature does not land on one arm.
- **Joules per token** = NVML total-energy counter over the timed window, divided by tokens.
  The counter covers every visible GPU, so a second idle GPU adds the same idle power to both
  arms.
- **p99** is per prompt (a prompt's latency is its batch's generate time), pooled over repeats.
- **Agreement** is untimed and separate from the energy windows: the normal model writes
  48 tokens for each of 32 prompts, then both arms are fed that text and we compare their
  next-token argmax at every position. It is teacher-forced agreement, not a task score.
- Counters for active experts live on the GPU in both arms, so counting adds no host sync to
  one arm only.
- Before measuring, the hooked "off" arm is checked against the stock model; if they disagree
  the stage stops (a transformers version the hook does not fit).

## Labels and receipts

- **MEASURED** only when the energy counter moved in every window. If it did not, no receipt is
  written and the stage reports why (an ESTIMATE receipt would trip the kit's exit-40 check).
- **SIMULATED** for `--fake`: made-up numbers that exercise the plumbing.
- The receipt (`profile: moe_adaptive_topk`) is signed with the offline test key, never
  published. It never grades VALID: a handful of repeats on one card has no Welch test.
- "Joules per good answer" is left empty. Agreement is not a pass rate, so it is stored as
  `token_agreement_pct` next to the joules instead.

## Gates and approvals

A proposal is filed only if all three hold:

| gate | rule |
|---|---|
| quality | token agreement with normal top-k at least 95% (`--adaptive-min-agree`) |
| p99 | adaptive p99 within +10% of normal |
| energy | joules per token lower than normal |

A failed gate still gives a receipt and a line in `summary.json`, but no proposal.

The stage is a `moe_routing` knob in the approvals system (see [APPROVALS.md](APPROVALS.md)).
The trial needs the grant bound `moe_routing: {min_mass, max_mass}`, which `--pre-approved`
covers like the other bounds. The proposal it files is **pending**: nothing is switched on, even
in a pre-approved run. A person approves and applies it (`wattnot proposals approve` then
`apply`), which writes `active/moe_routing.json`. Without that file the setting is off.

## Optional means optional

The stage runs last, and only with `--adaptive-topk`. It has a time box (`--adaptive-budget-s`,
default 1500 s) and finishes whole pairs of runs, marking the receipt `truncated` if it stops
early. If it fails, is skipped, or finds nothing, the rest of the run is unaffected and the exit
code stays 0/10/20/30/40/50 exactly as before. If an earlier stage fails, the run stops there and
the stage does not start.

## Limits

- One MoE model, one card, one load shape, eager transformers loop.
- Not a vLLM result, not a throughput result, not a site or grid saving, no carbon claim.
- Agreement with the normal model is not task accuracy.
- The stage stops vLLM and restores the power cap before it loads the model (vLLM holds most of
  the GPU memory).
- Nothing in this stage has run on a GPU yet. The CPU result above is the only real number.
