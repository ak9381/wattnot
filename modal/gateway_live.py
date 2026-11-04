"""Modal: WattNot's gateway side by side (without vs with WattNot) in one A100-80GB container,
in front of our own vLLM serving Muse Glimmer 30B. NVML energy counter per arm (MEASURED).
Modal gives no root, so the power cap step is skipped and labelled "power cap not available".

Spends only with WATTNOT_MODAL_GO=1. Hard stop: container timeout CAP_S (worst case below).

    WATTNOT_MODAL_GO=1 modal run modal/gateway_live.py --workloads text,agent,images,voice --n 60
"""
from __future__ import annotations

import io
import os
import sys
import tarfile

APP_NAME = "wattnot-gateway-live"
GPU = "A100-80GB"
GPU_USD_H = 2.50                      # ESTIMATE, Modal list price (not a bill)
CAP_S = 80 * 60                       # hard container timeout: 80 min x $2.50 = $3.33 worst (ESTIMATE)
VOL = "wattnot-glimmer-weights"
MODEL = "meta-models/Muse-Glimmer-30B"
assert CAP_S / 3600 * GPU_USD_H <= 5.0

try:  # pragma: no cover - only with modal installed
    import modal
    app = modal.App(APP_NAME)
    weights = modal.Volume.from_name(VOL)
    image = (modal.Image.from_registry("vllm/vllm-openai:muse-glimmer")
             .dockerfile_commands(["RUN ln -sf \"$(command -v python3)\" /usr/local/bin/python"])
             .entrypoint([])
             .pip_install("pynvml", "jsonschema", "pyyaml", "numpy", "pillow", "httpx", "python-multipart",
                          "fastapi", "uvicorn", "cryptography", "langchain-core", "langchain-openai", "optuna>=3.6,<5")
             .add_local_dir("src", "/app/src")
             .add_local_dir("scripts", "/app/scripts")
             .add_local_dir("configs", "/app/configs")
             .add_local_dir("web", "/app/web")
             .add_local_dir("data/voice", "/app/data/voice")
             .add_local_dir("data/vision", "/app/data/vision")
             .add_local_file("pyproject.toml", "/app/pyproject.toml"))

    @app.function(gpu=GPU, timeout=CAP_S, scaledown_window=10, image=image, volumes={"/weights": weights},
                  secrets=[modal.Secret.from_name("wattnot-meta")])
    def run_gateway(workloads: str = "text,agent", n: int = 60) -> bytes:
        import glob
        import json
        import subprocess
        import threading
        import time
        import urllib.request
        threading.Timer(CAP_S - 120, lambda: os._exit(4)).start()      # backstop inside the container
        os.environ.update(HF_HUB_CACHE="/weights", HF_HOME="/weights", HF_HUB_OFFLINE="1",
                          VLLM_CACHE_ROOT="/weights/vllm-cache")
        snap = sorted(glob.glob("/weights/models--meta-models--Muse-Glimmer-30B/snapshots/*"))[-1]
        out = "/app/out/hosted/modal"
        os.makedirs(out, exist_ok=True)
        try:
            import pynvml as n_
            n_.nvmlInit(); h = n_.nvmlDeviceGetHandleByIndex(0)
            probe = {"gpu": n_.nvmlDeviceGetName(h), "limit_w": n_.nvmlDeviceGetPowerManagementLimit(h) // 1000}
            try:
                n_.nvmlDeviceSetPowerManagementLimit(h, n_.nvmlDeviceGetPowerManagementLimit(h)); probe["cap"] = "allowed"
            except Exception as e:  # noqa: BLE001
                probe["cap"] = f"refused: {type(e).__name__}"
        except Exception as e:  # noqa: BLE001
            probe = {"nvml": f"unavailable: {type(e).__name__}"}
        json.dump(probe, open(f"{out}/nvml_probe.json", "w"))
        print("nvml probe:", probe, flush=True)
        srv = subprocess.Popen(["vllm", "serve", snap, "--served-model-name", MODEL, "--reasoning-parser", "muse_glimmer",
                                "--max-model-len", "8192", "--enable-auto-tool-choice", "--tool-call-parser", "muse_glimmer",
                                "--gpu-memory-utilization", "0.90", "--max-num-seqs", "64", "--limit-mm-per-prompt.image", "1",
                                "--port", "8000", "--safetensors-load-strategy", "prefetch"],
                               stdout=open(f"{out}/vllm.log", "wb"), stderr=subprocess.STDOUT)
        t0 = time.time()
        while time.time() - t0 < 1200:
            if srv.poll() is not None:
                raise RuntimeError("vllm exited")
            try:
                urllib.request.urlopen("http://127.0.0.1:8000/v1/models", timeout=5); break
            except Exception:  # noqa: BLE001
                time.sleep(5)
        print(f"vllm ready in {time.time() - t0:.0f}s", flush=True)
        for _ in range(8):   # GPU warm-up, not measured: compile graphs and warm clocks before the arms
            body = {"model": MODEL, "messages": [{"role": "user", "content": "Count from 1 to 40."}], "max_tokens": 200}
            urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000/v1/chat/completions",
                                   data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}), timeout=300)
        p = subprocess.run([sys.executable, "scripts/e2e/hosted_gateway_run.py", "--provider", "modal",
                            "--config", "configs/gw-modal.yaml", "--workloads", workloads, "--n", str(n),
                            "--out", out, "--max-usd", "50", "--timeout-min", "25",
                            "--ledger", f"{out}/spend.jsonl"],
                           cwd="/app", env={**os.environ, "PYTHONPATH": "/app/src"}, capture_output=True, text=True,
                           timeout=CAP_S - (time.time() - t0) - 300)
        open(f"{out}/driver.log", "w").write(p.stdout + p.stderr)
        print(p.stdout[-6000:], p.stderr[-3000:], flush=True)
        srv.terminate()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            t.add(out, arcname="modal")
        return buf.getvalue()

    @app.local_entrypoint()
    def main(workloads: str = "text,agent", n: int = 60, dest: str = "out/hosted/modal-run"):
        if os.environ.get("WATTNOT_MODAL_GO") != "1":
            raise SystemExit("set WATTNOT_MODAL_GO=1 to spend")
        data = run_gateway.remote(workloads, n)
        os.makedirs(dest, exist_ok=True)
        tarfile.open(fileobj=io.BytesIO(data), mode="r:gz").extractall(dest)
        print("saved to", dest)
except ImportError:  # pragma: no cover
    pass
