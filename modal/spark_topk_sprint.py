"""DRAFT (unmerged, never deployed): Modal Spark Open top-k sprint.

Stages (each is a separate, opt-in command; nothing runs on import):
  python modal/spark_topk_sprint.py --plan          # print the plan + cost, no Modal call ($0)
  modal run modal/spark_topk_sprint.py::prefetch    # CPU container -> Volume, NVFP4 533 GB  (needs go)
  modal run modal/spark_topk_sprint.py::power_probe # 1xT4 ~60 s: can we read NVML power?    (needs go)
  modal run modal/spark_topk_sprint.py::smoke       # 1xL4, Llama 3.2 1B, same code path     (needs go)
  modal run modal/spark_topk_sprint.py::sprint      # 8xB200, top-k 8/6/4, checkpointed     (needs go)
"""
from __future__ import annotations
import json, sys

MODEL = "meta-models/Muse-Spark-Open-816B-A42B-NVFP4"   # 533 GB (HF API, 29 Oct), Meta official
SMOKE_MODEL = "meta-llama/Llama-3.2-1B-Instruct"
TOPKS = (8, 6, 4)                                        # 8 = stock
VOL, CKPT = "wattnot-spark-weights", "wattnot-spark-ckpt"
B200_8_USD_H = 52.0                                      # 8x$6.25 + CPU/mem, list price (ESTIMATE)

SCHEDULE = [  # (minute_from, minute_to, step, measured?)
    (0, 12, "container start, mount Volume, vLLM load NVFP4 TP=8 (cache warm)", False),
    (12, 15, "warmup + NVML power read check; record power.draw / energy counter", True),
    (15, 37, "top-k 8 (stock): 92-item tiers, J/correct, tok/s, p99, pass rate", True),
    (37, 42, "restart with --hf-overrides num_experts_per_tok=6", False),
    (42, 62, "top-k 6: same tiers", True),
    (62, 67, "restart with num_experts_per_tok=4", False),
    (67, 87, "top-k 4: same tiers", True),
    (87, 90, "write summary + receipt to ckpt Volume, exit (container stops billing)", False),
]


def plan() -> dict:
    mins = SCHEDULE[-1][1]
    return {"model": MODEL, "topks": TOPKS, "gpu": "B200:8", "minutes": mins,
            "usd_estimate": round(mins / 60 * B200_8_USD_H, 2), "hard_stop_usd": 85,
            "schedule": [dict(zip(("from", "to", "step", "measured"), s)) for s in SCHEDULE],
            "label": "PLAN (nothing deployed)"}


def serve_args(topk: int, model: str = MODEL, tp: int = 8) -> list[str]:
    a = ["vllm", "serve", model, "--tensor-parallel-size", str(tp), "--max-model-len", "8192",
         "--enable-expert-parallel", "--gpu-memory-utilization", "0.90",
         "--download-dir", "/weights"]
    if topk != 8:
        a += ["--hf-overrides", json.dumps({"num_experts_per_tok": topk})]
    return a


def done_arms(ckpt: dict) -> list[int]:
    """Preemption resume: arms already written to the ckpt Volume are skipped."""
    return [k for k in TOPKS if ckpt.get(str(k), {}).get("status") == "done"]


try:  # Modal definitions only if the SDK is installed; never deployed from this branch
    import modal  # type: ignore
    app = modal.App("wattnot-spark-topk-draft")
    image = (modal.Image.from_registry("vllm/vllm-openai:latest")
             .pip_install("huggingface_hub[hf_transfer]")
             .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
             .add_local_dir("workloads", "/app/workloads")
             .add_local_dir("src", "/app/src"))
    weights = modal.Volume.from_name(VOL, create_if_missing=True)
    ckpt_vol = modal.Volume.from_name(CKPT, create_if_missing=True)

    @app.function(image=image, cpu=8, memory=16384, timeout=4 * 3600,
                  volumes={"/weights": weights}, secrets=[modal.Secret.from_name("hf")])
    def prefetch():
        from huggingface_hub import snapshot_download
        snapshot_download(MODEL, cache_dir="/weights", max_workers=16)
        weights.commit()

    @app.function(image=image, gpu="T4", timeout=300)
    def power_probe():
        import subprocess
        return subprocess.run(["nvidia-smi", "--query-gpu=power.draw,power.limit,enforced.power.limit",
                               "--format=csv"], capture_output=True, text=True).stdout
except ImportError:
    app = None

if __name__ == "__main__":
    if "--plan" in sys.argv:
        print(json.dumps(plan(), indent=1))
