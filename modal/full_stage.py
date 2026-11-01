"""Modal stage, option B: our own vLLM serving Muse Glimmer 30B on one A100-80GB, every WattNot
lever the platform allows, MEASURED NVML energy. One function call = one round.

Nothing here spends unless WATTNOT_MODAL_GO=1 is set; the container has a hard timeout and a
kill thread inside it, so a hang cannot bill past ROUND_CAP_S. Billed to the workspace's Modal
hackathon credits. Weights come from the existing Volume (no download).

    WATTNOT_MODAL_GO=1 modal run modal/full_stage.py::round_ --round r13 [--limit 2]

The local orchestrator scripts/e2e/modal_full_round.py wraps this with the spend guard,
Spark grading, the signed receipt and teardown checks; use that, not this file directly.
"""
from __future__ import annotations

import io
import os
import sys
import tarfile

APP_NAME = "wattnot-full-stage"
GPU = "A100-80GB"
GPU_USD_H = 2.50              # ESTIMATE, Modal list price for A100-80GB (not a bill)
ROUND_CAP_S = 3 * 3600        # hard container timeout: worst case 3 h x $2.50 = $7.50 (ESTIMATE)
SMOKE_CAP_S = 22 * 60         # smoke tests: 22 min x $2.50 = $0.92 worst case (ESTIMATE)
VOL = "wattnot-glimmer-weights"
GO_ENV = "WATTNOT_MODAL_GO"
EXPECTED_MIN = {"server_starts": 5 * 4, "without": 30, "batch_kv": 12, "fp8": 6, "with": 15,
                "blocks": 25}  # ESTIMATE minutes per round


def estimate_round(minutes: float | None = None) -> dict:
    m = minutes if minutes is not None else sum(EXPECTED_MIN.values())
    return {"minutes": m, "usd": round(m / 60 * GPU_USD_H, 2), "worst_usd": round(ROUND_CAP_S / 3600 * GPU_USD_H, 2),
            "label": "ESTIMATE"}


def _require_go():
    if os.environ.get(GO_ENV) != "1":
        raise SystemExit(f"refusing: set {GO_ENV}=1 for a paid Modal GPU call")


def _import_modal():
    """The repo has a `modal/` folder; import the real modal SDK, not that namespace package."""
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    m = sys.modules.get("modal")
    if m is not None and not hasattr(m, "App"):
        del sys.modules["modal"]
    saved = sys.path[:]
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != repo]
    try:
        import modal as real
    finally:
        sys.path[:] = saved
    return real


try:  # pragma: no cover - only with modal installed
    modal = _import_modal()

    app = modal.App(APP_NAME)
    weights = modal.Volume.from_name(VOL)
    image = (modal.Image.from_registry("vllm/vllm-openai:muse-glimmer")
             .dockerfile_commands(["RUN ln -sf \"$(command -v python3)\" /usr/local/bin/python"])
             .entrypoint([])
             .pip_install("pynvml", "jsonschema", "pyyaml", "numpy")
             .add_local_dir("src", "/app/src")
             .add_local_dir("scripts", "/app/scripts")
             .add_local_dir("workloads", "/app/workloads")
             .add_local_dir("examples", "/app/examples")
             .add_local_dir("data/public_bench", "/app/data/public_bench")
             .add_local_dir("data/vision", "/app/data/vision")
             .add_local_dir("data/voice", "/app/data/voice")
             .add_local_file("pyproject.toml", "/app/pyproject.toml"))

    def _body(round_id: str, limit: int, skip: list[str], cap_s: int) -> bytes:
        import subprocess
        import threading
        sys.path[:0] = ["/app", "/app/src"]
        threading.Timer(cap_s - 120, lambda: os._exit(4)).start()   # container-side backstop
        os.environ["HF_HUB_CACHE"] = os.environ["HF_HOME"] = "/weights"
        os.environ["HF_HUB_OFFLINE"] = "1"
        from glob import glob
        snaps = sorted(glob("/weights/models--meta-models--Muse-Glimmer-30B/snapshots/*"))
        out = f"/tmp/results/{round_id}"
        cmd = [sys.executable, "/app/scripts/e2e/modal_full_inner.py", "--out", out,
               "--model-path", snaps[-1] if snaps else "meta-models/Muse-Glimmer-30B"]
        if limit:
            cmd += ["--limit", str(limit)]
        if skip:
            cmd += ["--skip", *skip]
        p = subprocess.run(cmd, cwd="/app", env={**os.environ, "PYTHONPATH": "/app:/app/src"},
                           capture_output=True, text=True)
        os.makedirs(out, exist_ok=True)
        open(f"{out}/inner.log", "w").write((p.stdout + p.stderr)[-200000:])
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            t.add(out, arcname=round_id)
        return buf.getvalue()

    @app.function(image=image, gpu=GPU, timeout=ROUND_CAP_S, volumes={"/weights": weights},
                  scaledown_window=10)
    def gpu_round(round_id: str, limit: int = 0, skip: list[str] | None = None) -> bytes:
        return _body(round_id, limit, skip or [], ROUND_CAP_S)

    @app.function(image=image, gpu=GPU, timeout=SMOKE_CAP_S, volumes={"/weights": weights},
                  scaledown_window=10)
    def gpu_smoke(round_id: str, limit: int = 1, skip: list[str] | None = None) -> bytes:
        return _body(round_id, limit, skip or [], SMOKE_CAP_S)

    @app.local_entrypoint()
    def round_(round: str, limit: int = 0, skip: str = "", out: str = "data/modal-full-1101/_incoming"):
        _require_go()
        fn = gpu_smoke if limit else gpu_round
        data = fn.remote(round, limit, [x for x in skip.split(",") if x])
        os.makedirs(out, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            t.extractall(out, filter="data")
        print(f"[full_stage] {round}: results in {out}/{round}")
except ImportError:  # pragma: no cover
    pass
