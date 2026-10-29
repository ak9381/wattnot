# Muse Spark Open on one H100: side demo and head to head

The model is Muse Spark Open 816B-A42B, a mixture of experts: 62 layers, 256 experts per layer, 8 used
per token. We run a 2-bit GGUF of it on one H100 80 GB with llama.cpp, then answer the same prompts on
Baseten's hosted Muse Spark Open, and pick the better backend per workload.

This is a side demo. It does not touch the main receipt schema, and nothing here rents a GPU.

## What the demo shows

1. **Place experts across three tiers** with `--n-cpu-moe N`: the experts of the first N layers sit in
   RAM; the other 62-N layers keep theirs in VRAM (hot). In llama.cpp the split is per layer, not per
   expert. At batch size 32 or more, RAM-resident experts are computed on the GPU through op offload
   (warm). At small batch they run on the CPU (cold).
2. **Tune N by trying it.** A small planner loop (seven loads at most, the baseline included) proposes
   the next N from numbers only: tok/s, J/token, which loads failed, which tries failed the quality gate.
   Muse Spark picks when the spend ledger allows. If the ledger is closed or its answer is out of range,
   a fixed policy decides. Every try is hash chained in `tune.json`.
3. **Head to head** with Baseten, same prompts, same fixed answer checks, same reasoning effort
   (low, thinking capped at 1500 tokens on both sides).
4. **Route per workload** and write the choice into the receipt.

## Labels: what is measured, what is not

| Number | Ours | Baseten |
|---|---|---|
| Quality (fixed answer checks) | MEASURED | MEASURED (REPLAY if read from a recording) |
| Latency, tokens, tok/s | MEASURED | MEASURED (REPLAY) |
| Cost per 1k answers | MEASURED seconds x the listed rental rate (INR 447.28/h incl. GST, assumed INR 88 per USD) | MEASURED tokens x list price ($1.25 / $0.15 cached / $4.25 per 1M); not invoice-checked |
| Energy per answer | MEASURED only if GPU (nvidia-smi) and host (RAPL: CPU package + DRAM) are both read. Without RAPL the host part is an ESTIMATE and no saving is claimed. | ESTIMATE range from a FLOP model. We cannot see their GPUs. |
| VRAM per setting | ESTIMATE (planner formula), checked by the load itself | n/a |

A GPU-only energy drop is never counted as a saving: moving experts to the CPU shifts watts to the host.

Each tune try needs the answer-check pass rate to drop by 1 point at most and p99 latency to stay
within 10% of the baseline. Exact text agreement is reported but does not decide: free text drifts a
little with batch size even at temperature 0.

## Router

Rules, in order (`spark_open/router.py`):

1. Gate. A backend is out if it made no answers, had failed calls, or scored more than 1 point below the
   better one. Private workloads exclude Baseten, whatever it costs.
2. Interactive: p99 inside the budget (default 20 s), lowest p99; within 10% on p99 the cheaper wins.
3. Batch: lowest cost per 1k answers; within 3% on cost, lower energy per answer wins (a MEASURED vs
   ESTIMATE gap is a rough guide); then local; then id.

The receipt (`h2h.json`) carries `routing.decisions[]` with the rule, every candidate row and its
labels, the chosen backend and the reason. `integrity.sha256` is recomputed after routing, and
`integrity.prev_sha256` points at the hash of `tune.json`.

Default profiles: `interactive`, `batch`, `batch+private`. Run it again on any saved receipt:

```bash
python -m wattnot.spark_open.router --h2h results/.../h2h.json --workload batch --private
```

## Dry run (CPU, no spend)

```bash
bash scripts/spark1gpu_build_llama.sh --cpu          # needs cmake and a C++ compiler
export PATH=/opt/llama.cpp/build/bin:$PATH
TINY_GGUF=/path/to/any-small.gguf bash scripts/spark1gpu_dryrun.sh
```

The tiny model stands in for the 816B one. Its tok/s, pass rate and cost say the harness works and
nothing about Spark Open on an H100. Baseten is REPLAYED from `data/cloud/recorded-smoke.json`
(6 of the 15 prompts are recorded; only those are compared).

## Real run

Rent the box yourself and stop it yourself. Nothing in this repo starts or stops a GPU.

Needs: 1x H100 80 GB, **about 250 GB RAM**, **at least 300 GB free disk** for UD-IQ2_S (275.5 GB, 7
shards) or **500 GB** to keep UD-IQ1_S (175.6 GB, 5 shards) as well. Sizes are MEASURED from the
Hugging Face tree listing on 27 Oct 2026.

```bash
export HF_TOKEN=...                         # read from the environment by `hf`, never printed
bash scripts/spark1gpu_build_llama.sh       # CUDA build for sm_90 if nvcc is found
bash scripts/spark1gpu_fetch.sh --dry-run   # the plan, nothing downloaded
export BASETEN_METRICS_KEY=...                     # only for --baseten live
bash scripts/spark1gpu_run.sh --baseten live
```

`spark1gpu_run.sh` refuses to start without a visible GPU. It fetches (IQ2_S first, IQ1_S only if IQ2_S
does not fit or fails), tunes, serves the best setting, runs the head to head, routes, and writes
`results/spark1gpu-<time>/{tune.json,h2h.json}`. Copy those back before you stop the box.

### Baseten spend

`--baseten live` makes one call per prompt (15 at most, effort low). Before each call the harness
prices the worst case (prompt plus the full max_tokens) and stops if the total already spent plus that
would pass **$0.50**. The running total is kept in `~/.wattnot/baseten_h2h_spend.json`. Six calls
measured $0.0047. All 15 should be about $0.012 (ESTIMATE). Calls are 4 s apart and only for the
`meta-models/muse-spark-open` model.

### Time and cost (ESTIMATE)

| Step | Time |
|---|---|
| Build llama.cpp (CUDA) | 10 to 15 min |
| Download 275.5 GB | 25 to 75 min at 1 to 0.4 Gbit/s |
| Tune: 7 loads of 276 GB at 3 to 10 min each, plus 6 prompts per try | 1 to 2 h |
| Head to head, local leg, 15 prompts at 3 to 8 tok/s | 10 to 30 min typical, 2 h worst case |
| Total | about 2.5 to 4.5 h |

At INR 447.28/h incl. GST that is about INR 1,100 to 2,000. The local decode rate on the real model is
not measured yet; the table is arithmetic from the planned range, not a result.

### Things that can go wrong

- **RAPL may be blocked on a rented VM.** The run then reports host energy as ESTIMATE and claims no
  saving. The latency, quality and cost numbers do not depend on it.
- **Loads that run out of VRAM** are recorded as failed tries and push the search toward more
  RAM-resident layers. IQ2_S plus the KV cache is tight: the planner formula (ESTIMATE) allows n_cpu_moe from about 49,
  so roughly 13 layers of experts in VRAM at most.
- **RAM.** IQ2_S is 275.5 GB; with 80 GB VRAM holding part of it, the RAM-resident experts need roughly
  250 GB. A smaller box should use `--quant UD-IQ1_S`.
- Cost per 1k answers on our side is for one request at a time. A busy server would batch and spend
  fewer seconds per answer. That is not measured here.
