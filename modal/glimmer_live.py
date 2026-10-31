"""Modal live bench: serve Muse Glimmer 30B with its OWN vLLM OpenAI server and MEASURE energy.

This file is the code for ONE paid Modal run. Importing it, or running ``--estimate`` /
``--preflight``, touches no Modal object, hits no GPU, downloads no weights and spends $0.
The single GPU bench only runs under ``run()`` with ``WATTNOT_MODAL_GO=1`` explicitly set
(the ``_require_go`` gate, copied from ``modal/spark_topk_sprint.py``). Everything the GPU
container measures is honest:

  * MEASURED  -- joules per request from NVML's total-energy counter
                 (nvmlDeviceGetTotalEnergyConsumption, millijoules) bracketing each HTTP
                 request; idle watts from nvmlDeviceGetPowerUsage sampled BEFORE the server
                 starts (truly idle); tokens/s from MEASURED latency.
  * ESTIMATE  -- the dollar cost (GPU $/hr list price x expected minutes). Never a bill.
  * SYNTHETIC -- the prompt text, when the repo replay set is not importable in the container.
                 The ENERGY around those prompts is still MEASURED.

CORRECTION TO PRIOR WORK: Muse Glimmer 30B IS servable, but ONLY with the custom vLLM image
``vllm/vllm-openai:muse-glimmer`` (vLLM 0.26.1rc1.dev608), via its OpenAI HTTP server started
with ``--reasoning-parser muse_glimmer``. We do NOT load ``from vllm import LLM`` in-process and
we do NOT use the stock ``vllm/vllm-openai:latest`` image: that architecture is multimodal and
only the pinned image registers it. The bench starts the server as a subprocess and talks HTTP.

IMAGE PULL NOTE: Docker Hub rate-limited the e2e pull on 2026-10-31. If ``from_registry`` hits a
Docker Hub rate limit, switch the ``from_registry`` argument below from ``GLIMMER_IMAGE`` to
``GLIMMER_IMAGE_MIRROR`` (a read-through gcr.io mirror of the same tag).

Design (mirrors spark_topk_sprint.py: modal.App, from_registry vLLM image, modal.Volume for
weights, the WATTNOT_MODAL_GO gate, Guard/Ledger/Price, kill_timer, cloud_timeout_s, a
container-side timeout):

  * ``prefetch_vol`` -- light CPU container, anonymously snapshot_downloads the PUBLIC repo
    meta-models/Muse-Glimmer-30B into the Volume with HF_HUB_ENABLE_HF_TRANSFER=1, then commits.
    Separate from the GPU bench so the expensive GPU never pays for the download.
  * ``bench`` -- the smallest fitting GPU (default A100-80GB). Samples idle watts, starts the
    pinned-image vLLM OpenAI server as a subprocess against the cached weights, polls
    /v1/models until ready, warms up once, then runs a fixed prompt set one at a time, bracketing
    EACH HTTP request with the NVML energy counter to get MEASURED joules/request. Returns a
    results dict; the server subprocess is killed in ``finally`` so billing ends.
  * Power caps are REFUSED on Modal (no privileged nvidia-smi power-limit control); the
    results record that honestly.

Commands (the caller runs these; this agent never does):
  python modal/glimmer_live.py --preflight                # free HTTP config check, $0, no GPU
  python modal/glimmer_live.py --estimate                 # print ESTIMATE cost, no Modal ($0)
  modal run modal/glimmer_live.py::estimate               # same, via Modal local entrypoint ($0)
  WATTNOT_MODAL_GO=1 modal run modal/glimmer_live.py::run  # the one live run (prefetch + bench)

``run`` writes results JSON to $WATTNOT_GLIMMER_OUT (default data/modal-live-1031/results.json).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# --- constants -------------------------------------------------------------------------------

MODEL = "meta-models/Muse-Glimmer-30B"          # PUBLIC, not gated; served name is the same
VOL = "wattnot-glimmer-weights"                 # Modal Volume the weights land in
APP_NAME = "wattnot-glimmer-live"
GO_ENV = "WATTNOT_MODAL_GO"                      # must be "1" to touch any paid/GPU Modal path
OUT_ENV = "WATTNOT_GLIMMER_OUT"                 # where run() writes the results JSON
DEFAULT_OUT = "data/modal-live-1031/results.json"

# The custom vLLM image that registers the muse_glimmer architecture (vLLM 0.26.1rc1.dev608).
# Stock vllm/vllm-openai:latest CANNOT load this multimodal model; only this tag can.
GLIMMER_IMAGE = "vllm/vllm-openai:muse-glimmer"
# Read-through mirror fallback if the Docker Hub pull is rate-limited (as it was on 2026-10-31).
# If from_registry(GLIMMER_IMAGE) hits a Docker Hub rate limit, switch the arg to this mirror.
GLIMMER_IMAGE_MIRROR = "mirror.gcr.io/vllm/vllm-openai:muse-glimmer"

SERVE_PORT = 8000                               # vLLM OpenAI server port inside the container

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
SERVE_DEADLINE_S = 15 * 60                       # /v1/models must answer by minute 15 (< 25-min cap)
MAX_MODEL_LEN = 8192                             # proven serve config
GPU_MEM_UTIL = 0.90                              # proven serve config
MAX_NUM_SEQS = 64                               # proven serve config
GEN_MAX_TOKENS = 128                            # modest answer length for short SYNTHETIC prompts

# Cost plan (ESTIMATE). Conservative expected minutes for each stage.
PREFETCH_MIN = 25.0                              # ~55.5 GiB pull with hf_transfer (ESTIMATE)
BENCH_MIN = 20.0                                 # server start + idle sample + ~24 requests (ESTIMATE)
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


# --- preflight: is this model servable? (free, local; gates all spend) -----------------------

# vLLM serves models by matching the HF config `architectures` to a registered implementation.
# The plain path is single-stream causal LM (architecture name ends in "ForCausalLM"). Muse
# Glimmer is NOT that path -- it is a custom multimodal architecture (model_type "muse_glimmer")
# -- but we have PROVEN that the pinned custom image vllm/vllm-openai:muse-glimmer registers and
# serves it over its OpenAI HTTP server with --reasoning-parser muse_glimmer. So a model whose
# model_type is in SERVABLE_WITH_IMAGE is servable BECAUSE that image ships its implementation,
# even though the structural heuristic alone would reject it. Genuinely-unknown custom
# architectures (not a causal LM, not in the allowlist) are still refused so we never pay to
# download tens of GiB and fail at engine init, and never silently substitute a different model.
_CAUSAL_LM_SUFFIX = "ForCausalLM"
SERVABLE_WITH_IMAGE = {"muse_glimmer": GLIMMER_IMAGE}   # model_type -> required custom image


def preflight_servable(config: dict) -> dict:
    """Decide -- from a model's HF config dict -- whether vLLM can serve it. Pure, no network,
    no vendor names.

    Returns ``{"ok", "architecture", "model_type", "reason", "multimodal", "image"}``. Used by
    run() to refuse an unservable model before any paid download/GPU.
    """
    arch = (config.get("architectures") or [None])[0]
    mtype = config.get("model_type")
    multimodal = bool(config.get("vision_config") or config.get("image_token_id") is not None)

    required_image = SERVABLE_WITH_IMAGE.get(str(mtype or ""))
    if required_image is not None:
        # Proven: the pinned custom image registers this architecture and serves it over HTTP.
        return {"ok": True, "architecture": arch, "model_type": mtype,
                "multimodal": multimodal, "image": required_image,
                "reason": (f"model_type {mtype!r} is served by the pinned custom image "
                           f"{required_image!r} (proven: vLLM OpenAI server with "
                           f"--reasoning-parser muse_glimmer). Servable.")}

    ok = str(arch or "").endswith(_CAUSAL_LM_SUFFIX) and not multimodal
    if ok:
        reason = f"architecture {arch!r} is a single-stream causal-LM vLLM can serve"
    else:
        reason = (f"architecture {arch!r} / model_type {mtype!r} is not a plain causal-LM and "
                  f"is not in the custom-image allowlist"
                  + (" (has a vision/image config -> multimodal)" if multimodal else "")
                  + "; vLLM cannot load it. Refusing to spend on an unservable model.")
    return {"ok": ok, "architecture": arch, "model_type": mtype,
            "multimodal": multimodal, "image": None, "reason": reason}


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
            return [{"id": it.id, "prompt": it.prompt,
                     "max_tokens": min(it.max_tokens, GEN_MAX_TOKENS),
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
                    "max_tokens": GEN_MAX_TOKENS, "prompt_source": "SYNTHETIC"})
    return out


# --- the request body (thinking OFF for short answers) ---------------------------------------

def chat_body(prompt: str, max_tokens: int, *, model: str = MODEL, reasoning_apply: bool = True) -> dict:
    """The OpenAI chat-completions body for ONE request. Thinking is LOW/OFF: reasoning_effort
    "low", plus chat_template_kwargs {"enable_thinking": false} UNLESS a prior request proved the
    server rejects that field (then `reasoning_apply=False` drops it but keeps reasoning_effort).

    Deterministic: temperature 0, seed 0, top_k -1.
    """
    body = {
        "model": model,
        "temperature": 0,
        "seed": 0,
        "top_k": -1,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "reasoning_effort": "low",
    }
    if reasoning_apply:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    return body


# --- the measurement core (pure given injected deps; mirrors the old measure()) --------------

def measure_http(post, nvml, handle, prompts: list[dict], model: str, *,
                 reasoning_apply: bool = True, clock=time.perf_counter) -> dict:
    """Run each prompt once over HTTP, bracketing it with the NVML total-energy counter (mJ) to
    get MEASURED joules/request, plus MEASURED latency and completion tokens -> tokens/s.

    Pure and testable: `post(body) -> (status, data)` is the injected HTTP transport (data is a
    parsed chat-completion dict); `nvml` only needs ``total_energy_mj(handle) -> int``. No Modal,
    no real GPU, no socket required for a unit test.

    One-time thinking fallback: if the FIRST request errors (status >= 400) with an error whose
    text mentions the chat_template_kwargs / enable_thinking field, we drop that field and retry
    every request with reasoning_effort "low" only. The mode used is returned as `thinking_mode`.

    MEASURED J for request k = (energy_mj_after - energy_mj_before) / 1000.
    """
    per_request = []
    first = True
    for p in prompts:
        body = chat_body(p["prompt"], p["max_tokens"], model=model, reasoning_apply=reasoning_apply)
        e0 = nvml.total_energy_mj(handle)
        t0 = clock()
        status, data = post(body)
        latency_s = clock() - t0
        e1 = nvml.total_energy_mj(handle)

        # One-time fallback: first request rejected specifically for the thinking field.
        if first and status is not None and status >= 400 and _is_thinking_field_error(data):
            reasoning_apply = False
            body = chat_body(p["prompt"], p["max_tokens"], model=model, reasoning_apply=False)
            e0 = nvml.total_energy_mj(handle)
            t0 = clock()
            status, data = post(body)
            latency_s = clock() - t0
            e1 = nvml.total_energy_mj(handle)
        first = False

        completion_tokens, prompt_tokens, reasoning_toks = _usage(data)
        answer = _content(data)
        j = max(0, e1 - e0) / 1000.0            # MEASURED joules this request (mJ -> J)
        per_request.append({
            "id": p["id"], "prompt_source": p["prompt_source"],
            "status": status,
            "j_measured": round(j, 3), "latency_s": round(latency_s, 4),
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "reasoning_tokens": reasoning_toks,
            "answer_chars": len(answer or ""),
        })
    n = len(per_request)
    total_j = sum(r["j_measured"] for r in per_request)
    total_completion = sum(r["completion_tokens"] for r in per_request)
    total_latency = sum(r["latency_s"] for r in per_request)
    total_reasoning = sum((r["reasoning_tokens"] or 0) for r in per_request)
    return {
        "n_requests": n,
        "mean_j_per_request": round(total_j / n, 3) if n else 0.0,
        "tokens_per_s": round(total_completion / total_latency, 3) if total_latency > 0 else 0.0,
        "total_j_measured": round(total_j, 3),
        "total_completion_tokens": total_completion,
        "total_reasoning_tokens": total_reasoning,
        "thinking_mode": "enable_thinking:false+effort:low" if reasoning_apply else "effort:low",
        "per_request": per_request,
        "energy_label": "MEASURED",
        "energy_method": "counter",      # nvmlDeviceGetTotalEnergyConsumption, per-request bracket
        "energy_source": "nvml",
    }


def _is_thinking_field_error(data) -> bool:
    """True when an error response blames the chat_template_kwargs / enable_thinking field."""
    txt = ""
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            txt = str(err.get("message") or "")
        elif isinstance(err, str):
            txt = err
        txt = txt or str(data.get("message") or "")
    else:
        txt = str(data or "")
    txt = txt.lower()
    return ("enable_thinking" in txt) or ("chat_template_kwargs" in txt)


def _usage(data) -> tuple[int, int, int | None]:
    """(completion_tokens, prompt_tokens, reasoning_tokens) from a chat-completion dict."""
    usage = (data.get("usage") or {}) if isinstance(data, dict) else {}
    details = usage.get("completion_tokens_details") or {}
    rt = details.get("reasoning_tokens")
    return (int(usage.get("completion_tokens") or 0),
            int(usage.get("prompt_tokens") or 0),
            int(rt) if isinstance(rt, int) else None)


def _content(data) -> str:
    """The answer text from choices[0].message.content."""
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices") or [{}]
    msg = (choices[0] or {}).get("message") or {}
    return msg.get("content") or ""


# --- Modal definitions (only if the real SDK is installed; never deployed from here) ----------
# The repo's own ``modal/`` dir can shadow the real SDK (no modal.App); that is fine -- the same
# guarded import keeps IMPORT free. Nothing below runs at import; the GPU path is GO-gated.

try:
    import modal  # type: ignore

    app = modal.App(APP_NAME)
    WEIGHTS_DIR = "/weights"

    # Light CPU image just for the weights prefetch (no vLLM needed to download).
    prefetch_image = (modal.Image.debian_slim(python_version="3.12")
                      .pip_install("huggingface_hub[hf_transfer]")
                      .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"}))

    # The bench image is the PINNED custom vLLM image (which registers muse_glimmer) + pynvml for
    # the energy counter. If from_registry(GLIMMER_IMAGE) hits a Docker Hub rate limit, switch the
    # argument to GLIMMER_IMAGE_MIRROR (see the IMAGE PULL NOTE at the top of this file).
    # The vLLM image ships python3 only; Modal's builder and runtime call `python`.
    bench_image = (modal.Image.from_registry(GLIMMER_IMAGE)
                   .dockerfile_commands(["RUN ln -sf \"$(command -v python3)\" /usr/local/bin/python"])
                   .entrypoint([])
                   .pip_install("pynvml")
                   .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
                   .add_local_dir("src", "/app/src")
                   .add_local_dir("workloads", "/app/workloads"))

    weights = modal.Volume.from_name(VOL, create_if_missing=True)

    @app.function(image=prefetch_image, cpu=8, memory=16384, timeout=PREFETCH_CAP_S,
                  volumes={WEIGHTS_DIR: weights})
    def prefetch_vol() -> dict:
        """CPU-only: snapshot_download Glimmer into the Volume with hf_transfer. The repo is
        PUBLIC and not gated, so the download is anonymous (no HF token / Modal secret needed)."""
        from huggingface_hub import snapshot_download
        path = snapshot_download(MODEL, cache_dir=WEIGHTS_DIR, max_workers=16)
        weights.commit()
        return {"download_dir": path}

    @app.function(image=bench_image, gpu=DEFAULT_GPU, timeout=BENCH_CAP_S,
                  scaledown_window=SCALEDOWN_S, volumes={WEIGHTS_DIR: weights})
    def bench(n_requests: int = N_REQUESTS) -> dict:  # pragma: no cover - runs only on a paid GPU
        """Start the pinned-image vLLM OpenAI server as a subprocess against the cached weights,
        sample idle watts BEFORE it starts, poll /v1/models until ready, warm up once, then run
        `n_requests` prompts one at a time with per-request NVML energy bracketing over HTTP.
        Returns the results dict; the server subprocess is killed in finally so the container
        stops and billing ends.

        A container-side kill thread enforces BENCH_CAP_S-60 even if the server wedges: nothing
        can run past the budget. Power caps are explicitly REFUSED (Modal gives no root
        power-limit control), recorded honestly in the result.
        """
        import os as _os
        import subprocess
        import threading
        import urllib.error
        import urllib.request

        # Container-side backstop: if anything hangs, exit hard well inside the Modal timeout.
        def _boom():
            _os._exit(4)
        bomb = threading.Timer(BENCH_CAP_S - 60, _boom)
        bomb.daemon = True
        bomb.start()

        base = f"http://127.0.0.1:{SERVE_PORT}"
        server = None
        log_path = "/tmp/vllm-serve.log"  # noqa: S108 - container-local tee target
        pynvml = None
        dev = None
        try:
            import pynvml  # noqa: F811

            pynvml.nvmlInit()
            dev = pynvml.nvmlDeviceGetHandleByIndex(0)
            gpu_name = pynvml.nvmlDeviceGetName(dev)
            if isinstance(gpu_name, bytes):
                gpu_name = gpu_name.decode()

            # MEASURED idle watts BEFORE the server starts (truly idle GPU).
            idle_w = float(pynvml.nvmlDeviceGetPowerUsage(dev)) / 1000.0

            # Point vLLM/HF at the cached weights on the Volume; resolve the snapshot dir if present
            # (else fall back to the repo id so vLLM pulls -- but prefetch should have cached it).
            _os.environ["HF_HUB_CACHE"] = WEIGHTS_DIR
            _os.environ["HF_HOME"] = WEIGHTS_DIR
            model_path = _resolve_model_dir(WEIGHTS_DIR) or MODEL

            # The PROVEN serve argv (vLLM OpenAI server, muse_glimmer reasoning parser).
            argv = [
                "vllm", "serve", model_path,
                "--served-model-name", MODEL,
                "--reasoning-parser", "muse_glimmer",
                "--max-model-len", str(MAX_MODEL_LEN),
                "--gpu-memory-utilization", str(GPU_MEM_UTIL),
                "--max-num-seqs", str(MAX_NUM_SEQS),
                "--limit-mm-per-prompt.image", "1",
                "--port", str(SERVE_PORT),
            ]
            logf = open(log_path, "wb")  # noqa: SIM115 - closed in finally
            server = subprocess.Popen(argv, stdout=logf, stderr=subprocess.STDOUT)

            # Tee the server log to the function logs so a failed start is visible.
            def _tee():
                try:
                    with open(log_path, "r", errors="replace") as f:
                        while server and server.poll() is None:
                            line = f.readline()
                            if line:
                                print("[vllm]", line.rstrip())
                            else:
                                time.sleep(0.5)
                except Exception:
                    pass
            tee = threading.Thread(target=_tee, daemon=True)
            tee.start()

            # Poll /v1/models until the server answers 200, or the deadline passes.
            ready = _wait_models_ready(base, SERVE_DEADLINE_S, clock=time.monotonic)
            if not ready:
                tail = _tail_file(log_path, 50)
                try:
                    pynvml.nvmlShutdown()
                except Exception:
                    pass
                return {
                    "ok": False, "model": MODEL, "gpu": gpu_name,
                    "idle_w_measured": round(idle_w, 2),
                    "error": "vllm server did not become ready before the deadline",
                    "serve_argv": argv, "server_log_tail": tail,
                    "served_via": "vllm-openai HTTP server (image vllm/vllm-openai:muse-glimmer)",
                    "caps": "refused",
                    "caps_reason": "no privileged power-limit control on Modal",
                    "labels": {"energy": "MEASURED", "idle_w": "MEASURED",
                               "tokens_per_s": "MEASURED", "cost": "ESTIMATE (see estimate_plan)"},
                }

            # A real HTTP transport for measure_http: returns (status, parsed-json).
            def _post(body: dict):
                req = urllib.request.Request(
                    f"{base}/v1/chat/completions",
                    data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310
                        return r.status, json.loads(r.read() or b"{}")
                except urllib.error.HTTPError as e:  # 4xx/5xx -> read the body for the error field
                    try:
                        data = json.loads(e.read() or b"{}")
                    except Exception:
                        data = {"error": {"message": str(e)}}
                    return e.code, data

            # A thin NVML shim so measure_http reads the counter the same way a test's fake does.
            class _Nvml:
                def total_energy_mj(self, h):
                    return int(pynvml.nvmlDeviceGetTotalEnergyConsumption(h))

            # One unmeasured warm-up chat request so the first MEASURED request is not load-skewed.
            _post(chat_body("ping", 8, model=MODEL, reasoning_apply=True))

            prompts = build_prompts(n_requests)
            results = measure_http(_post, _Nvml(), dev, prompts, MODEL, reasoning_apply=True)
            results.update({
                "ok": True, "model": MODEL, "gpu": gpu_name,
                "idle_w_measured": round(idle_w, 2),
                "max_model_len": MAX_MODEL_LEN,
                "gpu_memory_utilization": GPU_MEM_UTIL,
                "served_via": "vllm-openai HTTP server (image vllm/vllm-openai:muse-glimmer)",
                "serve_argv": argv,
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
            if server is not None:
                try:
                    server.terminate()
                    server.wait(timeout=20)
                except Exception:
                    try:
                        server.kill()
                    except Exception:
                        pass
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
        # Preflight BEFORE any spend. Muse Glimmer is accepted because the pinned custom image
        # serves it; a genuinely-unknown architecture would be refused here ($0 spent).
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
            prefetch_info = prefetch_vol.with_options(timeout=pf_timeout).remote()
            print("PREFETCH:", json.dumps(prefetch_info))

            bench_timeout = stage_timeout(BENCH_CAP_S, gpu_usd_h)
            results = bench.with_options(gpu=gpu, timeout=bench_timeout).remote(n_requests)
        finally:
            killer.cancel()

        results["estimate_cost"] = plan
        out_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"RESULTS -> {out_path}")
        print(json.dumps({k: results.get(k) for k in
                          ("gpu", "n_requests", "mean_j_per_request", "tokens_per_s",
                           "idle_w_measured", "thinking_mode", "caps", "energy_label")}, indent=2))

except (ImportError, AttributeError):  # real SDK missing, or the repo modal/ dir shadows it
    app = None


# --- helpers used by the GPU path (pure / stdlib; safe to import anywhere) --------------------

def _wait_models_ready(base: str, deadline_s: float, *, clock=time.monotonic) -> bool:  # pragma: no cover - needs a live server
    """Poll ``GET {base}/v1/models`` until it answers 200 or the deadline passes."""
    import urllib.request
    started = clock()
    url = f"{base}/v1/models"
    while clock() - started < deadline_s:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(5)
    return False


def _tail_file(path: str, n: int) -> list[str]:  # pragma: no cover - container-local log read
    """The last ~n lines of a file, for recording a failed server start."""
    try:
        with open(path, "r", errors="replace") as f:
            return [ln.rstrip() for ln in f.readlines()[-n:]]
    except Exception:
        return []


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
    # Pure local entry for the agent/CI: --preflight / --estimate spend $0. The live run goes
    # through `modal run modal/glimmer_live.py::run` with WATTNOT_MODAL_GO=1.
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
