# Joules per correct answer (J/correct), v0.1

One number for "how much energy did it take to get a right answer". Lower is better.

```
J/correct = (GPU energy for the run + overhead energy) / number of answers that passed the check
```

## What is included

- **GPU energy of the serving model.** Read from the NVIDIA NVML energy counter, sampled 10 times a
  second, for the whole request window (prefill and decode). Idle time between runs is not counted.
- **Overhead energy, when it runs on metered hardware:** the small-model pass before an escalation,
  the quality judge, and any re-check. These are added to the numerator, so the figure is *net*.
- **Escalations.** In small-model-first mode both the small attempt and the big retry count.

## What is excluded (for now)

- CPU, memory, network, cooling and the rest of the building. Multiply by your PUE if you want site
  energy; we report GPU energy only.
- Hosted model calls (the planner, hosted endpoints). Providers report tokens, not joules, so these
  show as "not metered". A hosted row on the leaderboard is an ESTIMATE: tokens x a published
  joules-per-token figure, and it says so.
- Embodied energy of the hardware.

## What counts as correct

An answer is correct when it passes the workload's fixed check:

- **math:** the final number after `ANSWER:` matches the reference;
- **code:** the function passes the unit tests that ship with the task;
- **qa:** the answer matches the reference after normalising case and whitespace.

RUN2 (proof-v1: 50 math, 29 code, 32 qa prompts, 666 checked answers per arm) used these fixed
checks. Rows that use a model as the judge (Llama 3.1 8B Instruct) are a separate era and are
never mixed with RUN2 rows.

## Sample size and error bars

- Each setup runs **3 repeats** of the full workload, interleaved with the baseline so drift hits
  both arms.
- We report the **mean ± standard deviation** of J/correct over the repeats, and in the receipt a
  95% bootstrap interval (10,000 resamples, seed 7) and a Welch t-test on the energy change.
- A setup only counts as better when the change passes the noise gate *and* the speed gate
  (p99 latency may rise at most 10%) *and* the quality gate (pass rate within the limit).

## Example (RUN2, 350 W cap, MEASURED)

| | Default vLLM (700 W) | 350 W cap |
|---|---|---|
| J per answer | 15.96 | 11.64 |
| pass rate | 93.69% | 93.69% |
| **J/correct** | **17.03** | **12.42 (-27.1%)** |
| p99 latency | baseline | +7.7% |

The signed receipt is `receipts/run2/rcpt_20261027_run2_cap350.json`. Check it with

```bash
wattnot verify receipts/run2/
```

Its `joules` block holds the formula and both arms; `measurement.spark_trace_id` points at the
planner's event log and `measurement.per_model_joules` splits energy by model.

## Mapping to the Green Software Foundation SCI

SCI = ((E x I) + M) per R.

| SCI term | Here |
|---|---|
| E, energy | GPU energy from NVML (plus metered overhead), in kWh = J / 3.6e6 |
| I, carbon intensity | not part of J/correct; multiply by your grid's gCO2e/kWh to get SCI |
| M, embodied | excluded in v0.1 |
| R, functional unit | **one correct answer** (not one request) |

So J/correct is the energy half of an SCI score whose functional unit is a correct answer.
Choosing "correct answer" rather than "request" stops a setup from looking efficient by answering
badly.

## Submitting a result

1. Run your setup on the same workload, 3 repeats, with the baseline interleaved.
2. Produce a receipt (`wattnot receipt build`, then `wattnot receipt sign`).
3. Fill a JSON file in the shape of `data/leaderboard/submission.example.json`:
   setup, model, hardware, workload, n_checked, repeats, sample_hz, j_per_correct (mean, sd),
   overhead_j_per_correct, pass_pct, p99_change_pct, escalation_rate_pct, small_model_pass_pct,
   label (MEASURED or ESTIMATE) and the receipt path.
4. Open a pull request adding both files under `data/leaderboard/submissions/`. We run
   `wattnot verify` on the receipt; a row is listed only if it verifies.

`scripts/reproduce_leaderboard.sh` rebuilds the current table from the recorded files with no GPU
and no API key.

## Changes

- v0.1 (2026-10-28): first version.
