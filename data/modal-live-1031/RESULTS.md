# Modal live bench — Muse Glimmer 30B (Task 1)

**Outcome: STOPPED before any spend. The model is not servable by vLLM. $0 spent on Modal.**

## What was checked (all free, read-only)

| Check | Result | Label |
|---|---|---|
| Weights accessible on HF | yes — public, not gated | MEASURED (HF API) |
| Weight size | 59.55 GB (≈55.5 GiB) bf16, 2 safetensors shards | MEASURED (HF API) |
| Architecture | `MuseGlimmerForConditionalGeneration`, `model_type: muse_glimmer` | MEASURED (config.json) |
| Multimodal? | yes — vision tower + projector, `image_token_id`/`video_token_id` | MEASURED (config.json) |
| transformers version pinned | `5.15.0.dev0` (does not exist) | MEASURED (config.json) |
| **Servable by vLLM?** | **no** | MEASURED |

## Why it was stopped

Muse Glimmer 30B is a **custom multimodal (vision-language) model**. Its text tower is a bespoke
architecture (per-layer RoPE theta, mixed sliding/full attention, final-logit softcapping,
`qk_scale_factor`, `post_feedforward_layernorm`) and the config declares
`architectures: ["MuseGlimmerForConditionalGeneration"]` / `model_type: "muse_glimmer"` against
`transformers_version 5.15.0.dev0`.

vLLM serves a model only if it registers that architecture. `MuseGlimmerForConditionalGeneration`
is **not** in vLLM's registry, and released `transformers` has no `muse_glimmer` modeling code, so
neither vLLM's native path nor its transformers fallback can load it. A live run would have
downloaded ~55 GiB to a Modal Volume and then failed at engine init — paying for nothing.

The task's rule is explicit: *"If weights are inaccessible, stop and report why rather than
substituting a non-Meta model."* The weights are downloadable but **unservable**, which is the same
situation. We did **not** substitute Llama or Spark Open to manufacture J/request numbers.

## Measured numbers

None — the bench did not run because the model cannot be served. `mean_j_per_request`, `tokens_per_s`
and `idle_w` are therefore **N/A (not run)**, not estimated or faked.

## Cost

- **Modal spend: $0.00 (MEASURED).** No Modal app or function was ever launched; `modal app list`
  and `modal container list` show nothing running.
- **Had it been servable, the ESTIMATE was ~$1.13** (A100-80GB: prefetch $0.21 + bench $0.92;
  H100 alternative ~$1.71) — well under the $5 hard cap. See `preflight.json`.

## The code is still a complete deliverable

- `modal/glimmer_live.py` — Modal app that would serve our own vLLM running Glimmer 30B bf16 on the
  smallest fitting 80 GB GPU (A100-80GB default, H100 option), sampling
  `nvmlDeviceGetTotalEnergyConsumption` around each request for **MEASURED J/request**, recording
  caps as **refused** (no privileged power-limit control on Modal), auto-exiting after the run.
  GO-gated (`WATTNOT_MODAL_GO=1`), ≤25-min container timeout, local + container-side kill timers,
  and a **preflight that refuses an unservable architecture before any download/GPU**.
- `src/wattnot/headline.py::ModalBackend` — was `NotImplementedError`; now a real backend
  (client → vLLM completions endpoint, meter → NVML energy counter endpoint, MEASURED/ESTIMATE
  labelling), unit-tested offline with fakes.

## How to reproduce the stop ($0)

```
python modal/glimmer_live.py --preflight     # prints {"ok": false, ...}  (HTTP GET of config.json)
python modal/glimmer_live.py --estimate      # prints the ESTIMATE cost, deploys nothing
```

A live run (`WATTNOT_MODAL_GO=1 modal run modal/glimmer_live.py::run`) refuses at the preflight
step for this model and spends $0.
