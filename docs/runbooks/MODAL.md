# Modal (hosted placement)

Every compare runs three placements on the same prompts: our own GPU (adaptive top-k,
tiered k-means experts, quantization), Baseten hosted and Modal hosted. The router then
picks one per workload, gated on quality and p99, and writes the choice and its reasons
into the receipt (`routing`). `placements[]` always lists all three; one that did not run
says why instead of showing a number.

## What Modal needs

1. **Credits.** Claim the hackathon credits on the workspace first. Until then, only run the
   dry run below.
2. **Workspace.** `akashkraju218` (check with `modal token info`).
3. **CLI auth.** `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` (or `~/.modal.toml`). These only
   drive the CLI. They are never sent to an endpoint.
4. **Endpoint.** `bash scripts/modal_deploy.sh --yes` creates a **Shared Endpoint** for
   `meta-models/Muse-Spark-Open-816B-A42B`. That's the same model as on Baseten, and Modal
   runs the GPUs, so there is no GPU type to pick and no idle cost.
   `--dedicated --yes` creates a Dedicated Endpoint for `meta-models/Muse-Glimmer-30B`
   instead (billed GPU seconds, scales to zero).
5. **Proxy Token** for calls: `modal workspace proxy-tokens create`, once.
6. **Env for WattNot runs:**
   ```bash
   export MODAL_ENDPOINT_URL=...            # from `modal endpoint info wattnot-spark-open`
   export MODAL_PROXY_TOKEN_ID=wk-...       # never commit or print these
   export MODAL_PROXY_TOKEN_SECRET=ws-...
   export MODAL_MODEL=meta-models/Muse-Spark-Open-816B-A42B   # or what /v1/models returns
   ```

## Cost (list prices read 2026-10-28, ESTIMATE until a bill says otherwise)

- Shared Muse Spark Open: $1.25 in / $0.15 cached / $4.25 out per 1M tokens, the same as
  Baseten. The 15-prompt compare at low effort is well under $0.05. Per-token use is not
  covered by Starter's included $30/month compute.
- Dedicated (H100 SXM5): $0.001097/s, about **$3.95/h** per container while it is up,
  including load time and 60 s of idle by default. L40S is about $1.95/h.

## Running it

```bash
python -m wattnot.spark_open.h2h --out out/compare            # all three; Modal runs when env is set
python -m wattnot.spark_open.h2h --modal live --out out/compare
wattnot cloud tune --live --provider modal                     # effort tuning on the Modal endpoint
python -m wattnot.proof.orchestrate --planner modal-open ...   # Spark Open on Modal picks the caps
```

Receipts carry the same fields as Baseten: `provider`, `model`, `endpoint`, `key_env`,
and in the planner trace `call.{provider, model, prompt_sha256, latency_ms, fallback}`.
