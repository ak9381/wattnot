"""Modal endpoint for the dashboard's Go live on Modal: our own vLLM serving Muse Glimmer 30B on
one A100-80GB, plus /wattnot/nvml so the dashboard reads the container's NVML energy counter
(MEASURED). Modal gives no root, so the power limit cannot be set: the cap step is skipped.

Behind Modal proxy auth (Modal-Key / Modal-Secret headers). Spend controls: one container max,
scales to zero after SCALEDOWN_S idle, each container lives at most CAP_S.

    modal deploy modal/glimmer_endpoint.py        # prints the URL; put it in configs/gw-modal-remote.yaml
    modal app stop wattnot-glimmer-endpoint       # stop billing at once
"""

import os

APP_NAME = "wattnot-glimmer-endpoint"
GPU = "A100-80GB"
GPU_USD_H = 2.50                 # ESTIMATE, Modal list price (not a bill)
CAP_S = 60 * 60                  # one container lives at most 60 min: $2.50 worst per container (ESTIMATE)
SCALEDOWN_S = 10 * 60            # idle 10 min -> container stops, billing stops
MODEL = "meta-models/Muse-Glimmer-30B"
VOL = "wattnot-glimmer-weights"

try:  # pragma: no cover - only with modal installed
    import modal
    app = modal.App(APP_NAME)
    weights = modal.Volume.from_name(VOL)
    image = (modal.Image.from_registry("vllm/vllm-openai:muse-glimmer")
             .dockerfile_commands(["RUN ln -sf \"$(command -v python3)\" /usr/local/bin/python"])
             .entrypoint([])
             .pip_install("pynvml", "fastapi", "httpx"))

    @app.cls(gpu=GPU, timeout=CAP_S, scaledown_window=SCALEDOWN_S, max_containers=1, image=image,
             volumes={"/weights": weights})
    @modal.concurrent(max_inputs=32)
    class Glimmer:
        @modal.enter()
        def start(self):
            import glob
            import subprocess
            import time
            import urllib.request
            os.environ.update(HF_HUB_CACHE="/weights", HF_HOME="/weights", HF_HUB_OFFLINE="1",
                              VLLM_CACHE_ROOT="/weights/vllm-cache")
            snap = sorted(glob.glob("/weights/models--meta-models--Muse-Glimmer-30B/snapshots/*"))[-1]
            self.proc = subprocess.Popen(
                ["vllm", "serve", snap, "--served-model-name", MODEL, "--reasoning-parser", "muse_glimmer",
                 "--max-model-len", "8192", "--enable-auto-tool-choice", "--tool-call-parser", "muse_glimmer",
                 "--gpu-memory-utilization", "0.90", "--max-num-seqs", "64", "--limit-mm-per-prompt.image", "1",
                 "--port", "8000", "--safetensors-load-strategy", "prefetch"])
            t0 = time.time()
            while time.time() - t0 < 1200:
                if self.proc.poll() is not None:
                    raise RuntimeError("vllm exited")
                try:
                    urllib.request.urlopen("http://127.0.0.1:8000/v1/models", timeout=5)
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(5)
            import pynvml
            pynvml.nvmlInit()
            self.nv, self.h = pynvml, pynvml.nvmlDeviceGetHandleByIndex(0)

        @modal.exit()
        def stop(self):
            self.proc.terminate()

        @modal.asgi_app(requires_proxy_auth=True)
        def web(self):
            import httpx
            from fastapi import FastAPI, Request
            from fastapi.responses import JSONResponse
            api = FastAPI()
            cl = httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=600)

            @api.get("/wattnot/nvml")
            def nvml():
                nv, h = self.nv, self.h
                try:
                    nv.nvmlDeviceSetPowerManagementLimit(h, nv.nvmlDeviceGetPowerManagementLimit(h))
                    cap = "allowed"
                except Exception as e:  # noqa: BLE001
                    cap = f"refused: {type(e).__name__}"
                return {"energy_mj": nv.nvmlDeviceGetTotalEnergyConsumption(h),
                        "power_w": nv.nvmlDeviceGetPowerUsage(h) / 1000,
                        "limit_w": nv.nvmlDeviceGetPowerManagementLimit(h) // 1000,
                        "gpu": nv.nvmlDeviceGetName(h), "cap": cap, "label": "MEASURED"}

            @api.get("/v1/models")
            async def models():
                r = await cl.get("/v1/models")
                return JSONResponse(r.json(), r.status_code)

            @api.post("/v1/chat/completions")
            async def chat(req: Request):
                r = await cl.post("/v1/chat/completions", content=await req.body(),
                                  headers={"Content-Type": "application/json"})
                return JSONResponse(r.json(), r.status_code)
            return api
except ImportError:  # pragma: no cover
    pass
