"""Modal live bench: serve our OWN vLLM running Muse Glimmer 30B and MEASURE energy/request.

This file is the code for ONE paid Modal run. Importing it, or running ``estimate()``,
touches no Modal object, hits no GPU, downloads no weights and spends $0. The single
GPU bench only runs under ``run()`` with ``WATTNOT_MODAL_GO=1`` explicitly set (the
``_require_go`` gate, copied from ``modal/spark_topk_sprint.py``). Everything the GPU
container measures is honest:

  * MEASURED  -- joules per request from NVML's total-energy counter
                 (nvmlDeviceGetTotalEnergyConsumption, millijoules) bracketing each request;
                 idle watts from the same counter before load; tokens/s from MEASURED latency.
  * ESTIMATE  -- the dollar cost (GPU $/hr list price x expected minutes). Never a bill.
  * SYNTHETIC -- the prompt text, when the repo replay set is not importable in the container.
                 The ENERGY around those prompts is still MEASURED.

Design (mirrors spark_topk_sprint.py's patterns: modal.App, from_registry vLLM image,
modal.Volume for weights, the WATTNOT_MODAL_GO gate, Guard/Ledger/Price, kill_timer,
cloud_timeout_s, a container-side timeout):

  * ``prefetch_vol`` -- CPU-only container, snapshot_downloads meta-models/Muse-Glimmer-30B
    into the Volume with HF_HUB_ENABLE_HF_TRANSFER=1 (anonymous; the repo is public, not gated).
    Separate from the GPU bench so the expensive GPU never pays for the download.
  * ``bench`` -- the smallest fitting GPU (default A100-80GB; GPU is a parameter so the
    caller can switch to H100). Loads Glimmer bf16 with vLLM IN-PROCESS from the mounted
    Volume, samples idle watts, then runs a fixed prompt set one at a time, bracketing EACH
    request with the NVML energy counter to get MEASURED joules/request. Returns a results
    dict and exits (no long-lived server); the container stops and billing ends.
  * Power caps are REFUSED on Modal (no privileged nvidia-smi power-limit control); the
    results record that honestly.

Commands (the caller runs these; this agent never does):
  python modal/glimmer_live.py --estimate                 # print ESTIMATE cost, no Modal ($0)
  modal run modal/glimmer_live.py::estimate               # same, via Modal local entrypoint ($0)
  WATTNOT_MODAL_GO=1 modal run modal/glimmer_live.py::run  # the one live run (prefetch + bench)

``run`` writes results JSON to $WATTNOT_GLIMMER_OUT (default data/cloud/glimmer-live/results.json).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# --- constants -------------------------------------------------------------------------------

MODEL = "meta-models/Muse-Glimmer-30B"          # PUBLIC, not gated; 55.49 GiB bf16, 2 shards
VOL = "wattnot-glimmer-weights"                 # Modal Volume the weights land in
APP_NAME = "wattnot-glimmer-live"
GO_ENV = "WATTNOT_MODAL_GO"                      # must be "1" to touch any paid/GPU Modal path
OUT_ENV = "WATTNOT_GLIMMER_OUT"                 # where run() writes the results JSON
DEFAULT_OUT = "data/cloud/glimmer-live/results.json"

# The smallest GPU that fits 55.5 GiB bf16 weights + KV cache on 80 GB is the A100-80GB; H100
# is the fallback if an A100-80GB is unavailable. Both are single-GPU (tp=1). Default A100-80GB
# because it is cheaper and sufficient.
DEFAULT_GPU = "A100-80GB"
GPU_USD_H = {                                   # list-price ESTIMATE constants ($/hr), not a bill
    "A100-80GB": 2.75,                          # ESTIMATE
    "H100": 4.50,                               # ESTIMATE
}
PREFETCH_USD_H = 0.50                           # CPU container, ~8 cores + 16 GiB (ESTIMATE)

# Time budgets. The GPU bench's container timeout is capped well under the plan so a hang
# cannot run away; the local kill_timer is the backstop next to it.
BENCH_CAP_S = 25 * 60                            # 1500 s hard container timeout for the GPU bench
PREFETCH_CAP_S = 60 * 60                         # CPU download container timeout (1 h)
SCALEDOWN_S = 30                                 # container idles only briefly before scaledown
MAX_MODEL_LEN = 4096                             # modest KV cache so it fits beside 55.5 GiB weights
GPU_MEM_UTIL = 0.90

# Cost plan (ESTIMATE). Conservative expected minutes for each stage.
PREFETCH_MIN = 25.0                              # ~55.5 GiB pull with hf_transfer (ESTIMATE)
BENCH_MIN = 20.0                                 # load + idle sample + ~24 requests + exit (ESTIMATE)
HARD_USD = 5.0                                   # HARD STOP: refuse to plan a run over this

HARD_STOP_USD, SOFT_STOP_USD = 5.0, 4.0          # spend Guard caps for the live ledger
LEDGER = "data/spend/modal.glimmer.ledger.jsonl"

N_REQUESTS = 24                                  # default fixed request count for the bench


# --- pure cost estimate (no Modal, callable from tests) --------------------------------------

def estimate_plan(gpu: str = DEFAULT_GPU, *, n_requests: int = N_REQUESTS,
                  prefetch_min: float = PREFETCH_MIN, bench_min: float = BENCH_MIN) -> dict:
    """ESTIMATE dollar cost for the default plan. Pure arithmetic: no Modal, no network, $0.

    cost = prefetch(CPU $/hr x prefetch minutes) + bench(GPU $/hr x bench minutes).
    Every dollar here is labelled ESTIMATE (list price x wall minutes), never a bill.
    """
    if gpu not in GPU_USD_H:
        raise ValueError(f"unknown GPU {gpu!r}; known: {sorted(GPU_USD_H)}")
    gpu_usd_h = GPU_USD_H[gpu]
    prefetch_usd = round(PREFETCH_USD_H * prefetch_min / 60.0, 4)
    bench_usd = round(gpu_usd_h * bench_min / 60.0, 4)
    total = round(prefetch_usd + bench_usd, 4)
    return {
        "label": "ESTIMATE (list price x wall minutes; not a bill)",
        "model": MODEL, "gpu": gpu, "n_requests": n_requests,
        "prefetch": {"usd_per_hr": PREFETCH_USD_H, "minutes": prefetch_min, "usd": prefetch_usd},
        "bench": {"gpu_usd_per_hr": gpu_usd_h, "minutes": bench_min, "usd": bench_usd},
        "total_usd": total, "hard_usd": HARD_USD, "within_budget": total <= HARD_USD,
        "max_model_len": MAX_MODEL_LEN, "gpu_memory_utilization": GPU_MEM_UTIL,
    }


def estimate_hard_stop(gpu: str = DEFAULT_GPU, **kw) -> dict:
    """Return the plan, raising SystemExit if the ESTIMATE total exceeds the $5 hard cap."""
    plan = estimate_plan(gpu, **kw)
    if plan["total_usd"] > HARD_USD:
        raise SystemExit(
            f"glimmer_live: ESTIMATE ${plan['total_usd']:.2f} exceeds hard cap ${HARD_USD:.2f}; "
            f"refusing to plan this run. Lower minutes/requests or pick a cheaper GPU.")
    return plan


# --- preflight: is this model servable by vLLM? (free, local; gates all spend) ---------------

# vLLM serves models by matching the HF config `architectures`/`model_type` to a registered
# implementation. A model whose architecture vLLM does not register cannot be loaded -- the GPU
# would download tens of GiB and then fail at engine init. This conservative allowlist of common
# vLLM-supported text model_types lets run() refuse BEFORE spending a cent. (Extend as needed.)
VLLM_SUPPORTED_MODEL_TYPES = frozenset({
    "llama", "mistral", "mixtral", "qwen2", "qwen2_moe", "qwen3", "qwen3_moe",
    "gemma", "gemma2", "gemma3_text", "phi3", "phimoe", "gpt_neox", "falcon",
    "stablelm", "starcoder2", "dbrx", "cohere", "deepseek_v2", "deepseek_v3",
})


def preflight_servable(config: dict) -> dict:
    """Decide -- from a model's HF config dict -- whether vLLM can serve it. Pure, no network.

    Returns ``{"ok": bool, "architecture", "model_type", "reason", "multimodal"}``. Used by run()
    to refuse an unservable model before any paid download/GPU, so we never pay to discover the
    engine cannot load it, and never silently substitute a different model.
    """
    arch = (config.get("architectures") or [None])[0]
    mtype = config.get("model_type")
    multimodal = bool(config.get("vision_config") or config.get("image_token_id") is not None)
    ok = mtype in VLLM_SUPPORTED_MODEL_TYPES and not str(arch or "").endswith("ForConditionalGeneration")
    if ok:
        reason = f"model_type {mtype!r} is a vLLM-supported text architecture"
    else:
        reason = (f"architecture {arch!r} / model_type {mtype!r} is not a vLLM-registered text model"
                  + (" (multimodal/conditional-generation)" if multimodal else "")
                  + "; vLLM cannot load it. Refusing to spend on an unservable model.")
    return {"ok": ok, "architecture": arch, "model_type": mtype,
            "multimodal": multimodal, "reason": reason}


def fetch_config(model: str = MODEL, *, timeout_s: float = 25.0) -> dict:
    """Fetch a public HF repo's config.json over HTTP (free, no token, no GPU)."""
    import json as _json
    import urllib.request
    url = f"https://huggingface.co/{model}/resolve/main/config.json"
    with urllib.request.urlopen(url, timeout=timeout_s) as resp:  # noqa: S310
        return _json.loads(resp.read())


# --- the GO gate + spend guard (both local-only; the container ships no wattnot) -------------

def _require_go(action: str) -> None:
    """The one gate in front of every paid/GPU Modal path. Raises -- before any Modal API call --
    unless WATTNOT_MODAL_GO=1 is set. Import and ``--estimate`` stay free ($0, no GPU)."""
    if os.environ.get(GO_ENV) != "1":
        raise SystemExit(
            f"glimmer_live: refusing to {action} without {GO_ENV}=1. This starts a paid GPU "
            f"run. Export {GO_ENV}=1 to launch for real; otherwise use --estimate ($0, no GPU).")


def guard_for(usd_h: float, ledger: str = LEDGER):
    """The shared spend Guard for the live run: $5 hard / $4 soft, priced per GPU-second at the
    all-in rate. wattnot is imported here, locally only -- the container does not ship it."""
    from wattnot.spend.guard import GPU_SECONDS, Guard, Ledger, Price
    return Guard("modal", Price(GPU_SECONDS, usd_per_s=usd_h / 3600), Ledger(Path(ledger)),
                 hard_usd=HARD_STOP_USD, soft_usd=SOFT_STOP_USD)


def stage_timeout(cap_s: int, usd_h: float) -> int:
    """A Modal function's timeout: the smaller of the static cap and what the $5 spend guard
    still allows (cloud_timeout_s), so the cap holds even if the laptop dies."""
    from wattnot.spend.guard import cloud_timeout_s
    t = min(cap_s, cloud_timeout_s(guard_for(usd_h)))
    if t <= 0:
        raise SystemExit("glimmer_live: spend guard has no headroom left; not launching")
    return t


def kill_timer(seconds: int, app_name: str = APP_NAME, *, grace_s: int = 60, stop=None, exit_=None):
    """Local backstop next to the server-side timeout: stop the app and exit when time is up.
    `stop`/`exit_` are injectable only so the firing path is testable without stopping a real
    Modal app or killing the process."""
    import subprocess
    import threading

    def _stop():
        subprocess.run(["modal", "app", "stop", app_name], timeout=60, check=False)

    def fire():
        (stop or _stop)()
        (exit_ or os._exit)(3)
    t = threading.Timer(seconds + grace_s, fire)
    t.daemon = True
    t.start()
    return t


# --- the fixed prompt set (SYNTHETIC text; MEASURED energy) ----------------------------------

def build_prompts(n: int = N_REQUESTS) -> list[dict]:
    """A fixed, deterministic request set. Prefer a slice of the repo replay set if it imports
    in this container; otherwise fall back to a fixed SYNTHETIC prompt list. Either way the
    ENERGY measured around each request is MEASURED -- only the prompt TEXT is labelled.

    Returns ``[{"id", "prompt", "max_tokens", "prompt_source"}]``; prompt_source is
    "replay-v1" (real workload) or "SYNTHETIC".
    """
    try:  # the container may not ship the wattnot package / workloads dir -- be defensive
        from wattnot.runner.workload import load_replay_set
        items = list(load_replay_set())[:n]
        if items:
            return [{"id": it.id, "prompt": it.prompt, "max_tokens": min(it.max_tokens, 128),
                     "prompt_source": "replay-v1"} for it in items]
    except Exception:
        pass
    base = [
        "Summarize the water cycle in two sentences.",
        "List three common causes of slow database queries.",
        "Explain what a hash map is to a beginner.",
        "Write a haiku about autumn leaves.",
        "What are the trade-offs between TCP and UDP?",
        "Describe how a bicycle gear system works.",
        "Give two tips for writing clear commit messages.",
        "Explain the difference between latency and throughput.",
    ]
    out = []
    for i in range(n):
        out.append({"id": f"synthetic-{i:04d}", "prompt": base[i % len(base)],
                    "max_tokens": 128, "prompt_source": "SYNTHETIC"})
    return out


# --- the measurement core (runs inside the GPU container; pure given injected deps) ----------

def measure(llm, sampling, prompts: list[dict], nvml, handle, *, clock=time.perf_counter) -> dict:
    """Run each prompt once, bracketing it with the NVML total-energy counter (mJ) to get
    MEASURED joules/request, plus MEASURED latency and completion tokens -> tokens/s.

    Pure and testable: `llm` only needs ``.generate([prompt], sampling) -> [out]`` where
    ``out.outputs[0].text`` and ``out.outputs[0].token_ids`` exist; `nvml` only needs
    ``total_energy_mj(handle) -> int``. No Modal, no real GPU required for a unit test.

    MEASURED J for request k = (energy_mj_after - energy_mj_before) / 1000.
    """
    per_request = []
    for p in prompts:
        e0 = nvml.total_energy_mj(handle)
        t0 = clock()
        outs = llm.generate([p["prompt"]], sampling)
        latency_s = clock() - t0
        e1 = nvml.total_energy_mj(handle)
        gen = outs[0].outputs[0]
        completion_tokens = len(getattr(gen, "token_ids", []) or [])
        prompt_tokens = len(getattr(outs[0], "prompt_token_ids", []) or [])
        j = max(0, e1 - e0) / 1000.0            # MEASURED joules this request (mJ -> J)
        per_request.append({
            "id": p["id"], "prompt_source": p["prompt_source"],
            "j_measured": round(j, 3), "latency_s": round(latency_s, 4),
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        })
    n = len(per_request)
    total_j = sum(r["j_measured"] for r in per_request)
    total_completion = sum(r["completion_tokens"] for r in per_request)
    total_latency = sum(r["latency_s"] for r in per_request)
    return {
        "n_requests": n,
        "mean_j_per_request": round(total_j / n, 3) if n else 0.0,
        "tokens_per_s": round(total_completion / total_latency, 3) if total_latency > 0 else 0.0,
        "total_j_measured": round(total_j, 3),
        "total_completion_tokens": total_completion,
        "per_request": per_request,
        "energy_label": "MEASURED",
        "energy_method": "counter",      # nvmlDeviceGetTotalEnergyConsumption, per-request bracket
        "energy_source": "nvml",
    }


# --- Modal definitions (only if the real SDK is installed; never deployed from here) ----------
# The repo's own ``modal/`` dir can shadow the real SDK (no modal.App); that is fine -- the same
# guarded import keeps IMPORT free. Nothing below runs at import; the GPU path is GO-gated.

try:
    import modal  # type: ignore

    app = modal.App(APP_NAME)
    # Build the serving image from pip (vLLM's wheel brings its own torch + CUDA userspace; the
    # NVIDIA driver comes from Modal's GPU runtime). pynvml reads the energy counter; hf_transfer
    # speeds the weights pull.
    image = (modal.Image.debian_slim(python_version="3.12")
             .pip_install("vllm", "huggingface_hub[hf_transfer]", "pynvml")
             .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "VLLM_USE_V1": "1"})
             .add_local_dir("src", "/app/src")
             .add_local_dir("workloads", "/app/workloads"))
    weights = modal.Volume.from_name(VOL, create_if_missing=True)
    WEIGHTS_DIR = "/weights"

    @app.function(image=image, cpu=8, memory=16384, timeout=PREFETCH_CAP_S,
                  volumes={WEIGHTS_DIR: weights})
    def prefetch_vol() -> dict:
        """CPU-only: snapshot_download Glimmer bf16 into the Volume with hf_transfer. The repo is
        PUBLIC and not gated, so the download is anonymous (no HF token / Modal secret needed)."""
        from huggingface_hub import snapshot_download
        path = snapshot_download(MODEL, cache_dir=WEIGHTS_DIR, max_workers=16)
        weights.commit()
        return {"download_dir": path}

    @app.function(image=image, gpu=DEFAULT_GPU, timeout=BENCH_CAP_S,
                  scaledown_window=SCALEDOWN_S, volumes={WEIGHTS_DIR: weights})
    def bench(n_requests: int = N_REQUESTS) -> dict:  # pragma: no cover - runs only on a paid GPU
        """Load Glimmer bf16 in-process with vLLM from the mounted Volume, sample idle watts,
        then run `n_requests` prompts one at a time with per-request NVML energy bracketing.
        Returns the results dict and exits so the container stops (no long-lived server).

        A container-side kill thread enforces BENCH_CAP_S even if vLLM wedges: nothing can run
        past the budget. Power caps are explicitly REFUSED (Modal gives no root power-limit
        control), recorded honestly in the result.
        """
        import os as _os
        import threading

        # Container-side backstop: if anything hangs, exit hard well inside the Modal timeout.
        def _boom():
            _os._exit(4)
        bomb = threading.Timer(BENCH_CAP_S - 60, _boom)
        bomb.daemon = True
        bomb.start()
        try:
            import pynvml

            from vllm import LLM, SamplingParams

            pynvml.nvmlInit()
            dev = pynvml.nvmlDeviceGetHandleByIndex(0)
            gpu_name = pynvml.nvmlDeviceGetName(dev)
            if isinstance(gpu_name, bytes):
                gpu_name = gpu_name.decode()

            # MEASURED idle watts BEFORE load (counter not yet moved by inference).
            idle_w = float(pynvml.nvmlDeviceGetPowerUsage(dev)) / 1000.0

            # A thin NVML shim so measure() can read the energy counter the same way as a test.
            class _Nvml:
                def total_energy_mj(self, h):
                    return int(pynvml.nvmlDeviceGetTotalEnergyConsumption(h))

            # Find the downloaded snapshot dir inside the Volume (hf cache layout).
            model_path = _resolve_model_dir(WEIGHTS_DIR) or MODEL
            llm = LLM(model=model_path, dtype="bfloat16", max_model_len=MAX_MODEL_LEN,
                      gpu_memory_utilization=GPU_MEM_UTIL, enforce_eager=False,
                      download_dir=WEIGHTS_DIR, tensor_parallel_size=1)
            sampling = SamplingParams(temperature=0.0, max_tokens=128)

            prompts = build_prompts(n_requests)
            # Warm up once (not measured) so the first MEASURED request is not load-skewed.
            llm.generate([prompts[0]["prompt"]], sampling)

            results = measure(llm, sampling, prompts, _Nvml(), dev)
            results.update({
                "model": MODEL, "gpu": gpu_name,
                "idle_w_measured": round(idle_w, 2),
                "dtype": "bfloat16", "max_model_len": MAX_MODEL_LEN,
                "gpu_memory_utilization": GPU_MEM_UTIL,
                # Honest record: we could not and did not apply any power cap.
                "caps": "refused",
                "caps_reason": "no privileged power-limit control on Modal",
                "labels": {"energy": "MEASURED", "idle_w": "MEASURED",
                           "tokens_per_s": "MEASURED", "cost": "ESTIMATE (see estimate_plan)"},
            })
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
            return results
        finally:
            bomb.cancel()

    @app.local_entrypoint()
    def estimate(gpu: str = DEFAULT_GPU):
        """Print the ESTIMATE cost for the plan and exit. Deploys nothing, spends $0."""
        print(json.dumps(estimate_hard_stop(gpu), indent=2))

    @app.local_entrypoint()
    def run(gpu: str = DEFAULT_GPU, n_requests: int = N_REQUESTS):
        """The one live run: GO-gated prefetch + GPU bench, writes results JSON, prints a summary.

        Both a server-side timeout (via with_options) and a local kill_timer bound the run so a
        hang cannot exceed the budget. Refuses up front without WATTNOT_MODAL_GO=1.
        """
        _require_go("run the GPU bench for Muse Glimmer 30B")
        # Preflight BEFORE any spend: refuse an architecture vLLM cannot serve, rather than pay to
        # download ~55 GiB and fail at engine init (and never substitute a different model).
        pf = preflight_servable(fetch_config(MODEL))
        print("PREFLIGHT:", json.dumps(pf))
        if not pf["ok"]:
            raise SystemExit(f"glimmer_live: {pf['reason']} Nothing downloaded, $0 spent.")
        plan = estimate_hard_stop(gpu, n_requests=n_requests)
        print("PLAN:", json.dumps(plan))
        gpu_usd_h = GPU_USD_H[gpu]

        out_path = Path(os.environ.get(OUT_ENV, DEFAULT_OUT))
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Local backstop next to each function's own Modal timeout.
        killer = kill_timer(BENCH_CAP_S + PREFETCH_CAP_S)
        try:
            pf_timeout = stage_timeout(PREFETCH_CAP_S, PREFETCH_USD_H)
            pf = prefetch_vol.with_options(timeout=pf_timeout).remote()
            print("PREFETCH:", json.dumps(pf))

            bench_timeout = stage_timeout(BENCH_CAP_S, gpu_usd_h)
            results = bench.with_options(gpu=gpu, timeout=bench_timeout).remote(n_requests)
        finally:
            killer.cancel()

        results["estimate_cost"] = plan
        out_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"RESULTS -> {out_path}")
        print(json.dumps({k: results.get(k) for k in
                          ("gpu", "n_requests", "mean_j_per_request", "tokens_per_s",
                           "idle_w_measured", "caps", "energy_label")}, indent=2))

except (ImportError, AttributeError):  # real SDK missing, or the repo modal/ dir shadows it
    app = None


def _resolve_model_dir(root: str) -> str | None:
    """Find the snapshot dir of the downloaded model inside the hf cache under `root`.

    hf lays weights out at ``<root>/models--meta-models--Muse-Glimmer-30B/snapshots/<rev>/``.
    Returns that dir if a safetensors file is present, else None (vLLM then pulls by repo id).
    """
    base = Path(root) / f"models--{MODEL.replace('/', '--')}" / "snapshots"
    if not base.is_dir():
        return None
    for snap in sorted(base.iterdir(), reverse=True):
        if snap.is_dir() and any(snap.glob("*.safetensors")):
            return str(snap)
    return None


if __name__ == "__main__":
    # Pure local entry for the agent/CI: --estimate prints the ESTIMATE and spends $0. The live
    # run goes through `modal run modal/glimmer_live.py::run` with WATTNOT_MODAL_GO=1.
    if "--preflight" in sys.argv:
        # Free HTTP check: can vLLM serve this model? (gates all spend in run())
        print(json.dumps(preflight_servable(fetch_config(MODEL)), indent=2))
    elif "--estimate" in sys.argv:
        g = DEFAULT_GPU
        if "--gpu" in sys.argv:
            g = sys.argv[sys.argv.index("--gpu") + 1]
        print(json.dumps(estimate_hard_stop(g), indent=2))
    else:
        print("usage: python modal/glimmer_live.py [--preflight | --estimate [--gpu A100-80GB|H100]]")
        print("live run: WATTNOT_MODAL_GO=1 modal run modal/glimmer_live.py::run")
