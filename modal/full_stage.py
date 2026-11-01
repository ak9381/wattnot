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
ROUND_CAP_S = int(3.75 * 3600)  # hard container timeout: 225 min, worst 3.75 h x $2.50 = $9.38 (ESTIMATE)
ROUND_HARD_USD = 10.0         # a single round's worst case must never exceed this
SMOKE_CAP_S = 32 * 60         # smoke tests: 32 min x $2.50 = $1.33 worst case (ESTIMATE); margin under the $1.50 smoke cap for metering drift
SMOKE_HARD_USD = 1.50
assert SMOKE_CAP_S / 3600 * GPU_USD_H <= SMOKE_HARD_USD
VOL = "wattnot-glimmer-weights"
GO_ENV = "WATTNOT_MODAL_GO"
# ESTIMATE minutes per round, scaled from the 1 Nov A100 smoke (9 items) to 192 items
EXPECTED_MIN = {"server_starts": 15, "without": 50, "batch_kv": 15, "with": 30, "blocks": 30, "fp8": 12}

assert ROUND_CAP_S / 3600 * GPU_USD_H <= ROUND_HARD_USD, \
    f"round worst case {ROUND_CAP_S / 3600 * GPU_USD_H:.2f} exceeds hard cap ${ROUND_HARD_USD}"


def estimate_round(minutes: float | None = None) -> dict:
    m = minutes if minutes is not None else sum(EXPECTED_MIN.values())
    return {"minutes": m, "usd": round(m / 60 * GPU_USD_H, 2), "worst_usd": round(ROUND_CAP_S / 3600 * GPU_USD_H, 2),
            "label": "ESTIMATE"}


def arm_events_path(base: str, arm: str) -> str:
    """X.events.jsonl -> X.without.events.jsonl / X.with.events.jsonl (one dashboard clip per arm)."""
    return base.replace(".events.jsonl", f".{arm}.events.jsonl") if base.endswith(".events.jsonl") else f"{base}.{arm}"


def route_events(lines: list[str], base: str, state: dict) -> None:
    """Append event lines to the current arm's file; a with-WattNot run.started switches files.
    Stage-level lines before any arm go to both files."""
    import json as _j
    for ln in lines:
        try:
            ev = _j.loads(ln)
        except ValueError:
            continue
        if ev.get("type") == "run.started" and (ev.get("data") or {}).get("arm") == "with_wattnot":
            state["arm"] = "with"
        targets = [state.get("arm", "without")] if state.get("arm") or ev.get("type") != "stage.features" \
            else ["without", "with"]
        for t in targets:
            with open(arm_events_path(base, t), "a") as f:
                f.write(ln.rstrip("\n") + "\n")


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
             .pip_install("pynvml", "jsonschema", "pyyaml", "numpy", "langchain-core", "langchain-openai")
             .add_local_dir("src", "/app/src")
             .add_local_dir("scripts", "/app/scripts")
             .add_local_dir("workloads", "/app/workloads")
             .add_local_dir("examples", "/app/examples")
             .add_local_dir("data/public_bench", "/app/data/public_bench")
             .add_local_dir("data/vision", "/app/data/vision")
             .add_local_dir("data/voice", "/app/data/voice")
             .add_local_file("pyproject.toml", "/app/pyproject.toml"))

    def _tail_events(path: str, q, stop):
        """Stream the container's events.jsonl to the local graph page through a Modal Queue."""
        import time
        pos = 0
        while True:
            try:
                with open(path, "rb") as f:
                    f.seek(pos)
                    chunk = f.read()
                    cut = chunk.rfind(b"\n") + 1
                    pos += cut
                    lines = [x for x in chunk[:cut].decode("utf-8", "replace").splitlines() if x.strip()]
                if lines and q is not None:
                    q.put_many(lines)
            except Exception:  # noqa: BLE001 - the graph feed never stops the round
                pass
            if stop.is_set():
                return
            time.sleep(2)

    def _body(round_id: str, limit: int, skip: list[str], cap_s: int, q=None) -> bytes:
        import subprocess
        import threading
        sys.path[:0] = ["/app", "/app/src"]
        threading.Timer(cap_s - 90, lambda: os._exit(4)).start()    # last-resort backstop
        os.environ["HF_HUB_CACHE"] = os.environ["HF_HOME"] = "/weights"
        os.environ["HF_HUB_OFFLINE"] = "1"
        from glob import glob
        snaps = sorted(glob("/weights/models--meta-models--Muse-Glimmer-30B/snapshots/*"))
        if not snaps:   # offline: a repo id cannot load; stop before any server start
            raise RuntimeError("Glimmer weights not found in the wattnot-glimmer-weights volume")
        out = f"/tmp/results/{round_id}"
        cmd = [sys.executable, "/app/scripts/e2e/modal_full_inner.py", "--out", out,
               "--model-path", snaps[-1] if snaps else "meta-models/Muse-Glimmer-30B",
               "--deadline-s", str(cap_s - (300 if limit else 900))]   # optional steps skip; results still come back
        if limit:
            cmd += ["--limit", str(limit)]
        if skip:
            cmd += ["--skip", *skip]
        os.makedirs(out, exist_ok=True)
        stop = threading.Event()
        tail = threading.Thread(target=_tail_events, args=(f"{out}/events.jsonl", q, stop), daemon=True)
        tail.start()
        try:
            p = subprocess.run(cmd, cwd="/app", env={**os.environ, "PYTHONPATH": "/app:/app/src"},
                               capture_output=True, text=True, timeout=cap_s - (180 if limit else 420))
            log = p.stdout + p.stderr
        except subprocess.TimeoutExpired as e:   # vLLM child dies with the container
            log = f"[inner timeout]\n{e.stdout or ''}{e.stderr or ''}"
            subprocess.run(["pkill", "-f", "vllm serve"], check=False)
        stop.set()
        tail.join(10)
        for lg in glob(f"{out}/raw/vllm-*.log"):   # keep tar + return fast: last 2 MB of each server log
            if os.path.getsize(lg) > 2_000_000:
                with open(lg, "rb") as f:
                    f.seek(-2_000_000, 2)
                    keep = f.read()
                with open(lg, "wb") as f:
                    f.write(b"[truncated to the last 2 MB]\n" + keep)
        open(f"{out}/inner.log", "w").write(str(log)[-200000:])
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            t.add(out, arcname=round_id)
        return buf.getvalue()

    @app.function(image=image, gpu=GPU, timeout=ROUND_CAP_S, volumes={"/weights": weights},
                  scaledown_window=10)
    def gpu_round(round_id: str, limit: int = 0, skip: list[str] | None = None, q=None) -> bytes:
        return _body(round_id, limit, skip or [], ROUND_CAP_S, q)

    @app.function(image=image, gpu=GPU, timeout=SMOKE_CAP_S, volumes={"/weights": weights},
                  scaledown_window=10)
    def gpu_smoke(round_id: str, limit: int = 1, skip: list[str] | None = None, q=None) -> bytes:
        return _body(round_id, limit, skip or [], SMOKE_CAP_S, q)

    @app.local_entrypoint()
    def round_(round: str, limit: int = 0, skip: str = "", out: str = "data/modal-full-1101/_incoming",
               events: str = ""):
        _require_go()
        import time
        fn = gpu_smoke if limit else gpu_round
        with modal.Queue.ephemeral() as q:
            call = fn.spawn(round, limit, [x for x in skip.split(",") if x], q)
            data, st = None, {}
            while data is None:
                try:
                    lines = q.get_many(500, timeout=2)
                except Exception:  # noqa: BLE001
                    lines = []
                if lines and events:
                    route_events(lines, events, st)
                try:
                    data = call.get(timeout=0)
                except (TimeoutError, modal.exception.TimeoutError):
                    time.sleep(1)
            for _ in range(3):   # drain what is left
                try:
                    lines = q.get_many(5000, timeout=1)
                except Exception:  # noqa: BLE001
                    break
                if lines and events:
                    route_events(lines, events, st)
        os.makedirs(out, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            t.extractall(out, filter="data")
        print(f"[full_stage] {round}: results in {out}/{round}")
except ImportError:  # pragma: no cover
    pass
