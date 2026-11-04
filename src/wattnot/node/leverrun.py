"""One server lever (FP8 weights, FP8 KV cache or batching) on the self-hosted Glimmer container,
run from the Go live screen, side by side against stock, MEASURED.

* Traffic goes straight to the local vLLM (the `glimmer` container on :8000), never to a hosted
  API, so the lever is what is measured.
* The container is changed by name only (`docker rm -f glimmer` + `docker run`): no pkill
  patterns. Stock BF16 (max-num-seqs 64, kv auto, gpu util 0.90) is restored in a finally block
  on success, gate failure, Stop or error, and the restore is verified from /v1/models and the
  container's own args (docker inspect).
* The answer check (vllmlever.try_lever + AnswerCheck) is the quality gate: same answers, lower
  J/answer, p99 within the limit, or the lever is not kept.
* Water = energy x 0.36 L/kWh (WUE, ESTIMATE); cost = energy x US commercial electricity price
  (ESTIMATE). Energy is the NVML counter (MEASURED).
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import threading
import time
import urllib.request

from wattnot.node.answercheck import AnswerCheck, proof_prompts
from wattnot.node.vllmlever import LEVERS, try_lever
from wattnot.runner.vllm_server import TrialParams

MODEL = "meta-models/Muse-Glimmer-30B"
NAME = "glimmer"
IMAGE = os.environ.get("GLIMMER_IMAGE", "vllm/vllm-openai:muse-glimmer")
PORT = 8000
URL = f"http://127.0.0.1:{PORT}/v1"
WUE_L_PER_KWH = 0.36
USD_PER_KWH = 0.1387
UI_LEVERS = ("fp8", "kv_cache", "batching")
# Stock BF16 exactly as scripts/glimmer_serve.sh starts it (and as the running container shows).
STOCK = TrialParams(max_num_seqs=64, enable_prefix_caching=False, gpu_mem_util=0.9, kv_cache_dtype="auto")
STOCK_TAIL = ["--reasoning-parser", "muse_glimmer", "--enable-auto-tool-choice", "--tool-call-parser",
              "muse_glimmer", "--max-model-len", "8192"]
# Duration (ESTIMATE): stock round + 2 restarts (~6 min each on the H100) + lever round.
EST_MIN = {"fp8": 16, "kv_cache": 15, "batching": 15}


def glimmer_args(model: str, p: TrialParams) -> list[str]:
    """vllm serve argv for the Glimmer container ("vllm", "serve", model, flags...)."""
    a = ["vllm", "serve", model, "--port", str(PORT), "--served-model-name", model, *STOCK_TAIL,
         "--gpu-memory-utilization", f"{p.gpu_mem_util:.2f}", "--max-num-seqs", str(p.max_num_seqs),
         "--limit-mm-per-prompt.image", "1",
         "--enable-prefix-caching" if p.enable_prefix_caching else "--no-enable-prefix-caching"]
    if p.quantization != "none":
        a += ["--quantization", p.quantization]
    if p.kv_cache_dtype != "auto":
        a += ["--kv-cache-dtype", p.kv_cache_dtype]
    return a


def is_stock(argv: list[str]) -> list[str]:
    """Problems that make the running argv not stock BF16 (empty list = stock)."""
    bad, s = [], " ".join(argv or [])
    if f"--max-num-seqs 64" not in s:
        bad.append("max-num-seqs is not 64")
    if "--gpu-memory-utilization 0.90" not in s:
        bad.append("gpu-memory-utilization is not 0.90")
    if "--quantization" in s:
        bad.append("quantization flag present")
    if "--kv-cache-dtype" in s and "--kv-cache-dtype auto" not in s:
        bad.append("kv-cache-dtype is not auto")
    if MODEL not in s:
        bad.append("model is not Glimmer")
    return bad


class Docker:
    """The Glimmer container, by name. run(cmd) is subprocess.run; injectable for tests."""
    def __init__(self, run=subprocess.run):
        self.run = run

    def argv(self) -> list[str]:
        try:
            r = self.run(["docker", "inspect", NAME, "--format", "{{json .Config.Entrypoint}}|{{json .Config.Cmd}}"],
                         capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):   # no docker here
            return []
        if r.returncode != 0:
            return []
        ep, cmd = r.stdout.strip().split("|", 1)
        return (json.loads(ep) or []) + (json.loads(cmd) or [])

    def restart(self, argv: list[str]) -> None:
        if argv[:2] != ["vllm", "serve"]:
            raise ValueError("expected a vllm serve argv")
        self.run(["docker", "rm", "-f", NAME], capture_output=True, text=True, timeout=120)
        env = ["-e", "HF_TOKEN"] if os.environ.get("HF_TOKEN") else ["-e", "HF_HUB_OFFLINE=1"]
        r = self.run(["docker", "run", "-d", "--name", NAME, "--gpus", "device=0", "--ipc=host",
                      "-p", f"{PORT}:{PORT}", "-v", "/root/.cache/huggingface:/root/.cache/huggingface",
                      *env, IMAGE, *argv[2:]], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"docker run failed: {(r.stderr or '')[-300:]}")


def models_ok(url: str = URL, timeout: float = 5) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/models", timeout=timeout) as r:
            return any(m.get("id") == MODEL for m in json.load(r).get("data", []))
    except Exception:  # noqa: BLE001 - not up yet
        return False


def vllm_asker(url: str = URL, stop: threading.Event | None = None):
    def ask(prompt: str) -> str:
        if stop is not None and stop.is_set():
            raise RuntimeError("stopped from the UI")
        body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                           "temperature": 0, "max_tokens": 512}).encode()
        req = urllib.request.Request(f"{url}/chat/completions", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            d = json.load(r)
        return str(((d.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    return ask


def arm_totals(joules: float) -> dict:
    kwh = joules / 3.6e6
    return {"joules": round(joules, 1), "wh": round(kwh * 1000, 4),
            "usd": round(kwh * USD_PER_KWH, 6), "water_l": round(kwh * WUE_L_PER_KWH, 6),
            "labels": {"energy": "MEASURED (NVML)", "usd": "ESTIMATE (energy x $0.1387/kWh, EIA)",
                       "water_l": "ESTIMATE (energy x 0.36 L/kWh WUE)"}}


class _PhasedCheck(AnswerCheck):
    """AnswerCheck that tells the run which arm is on the GPU, so the sampler can split energy."""
    def __init__(self, run: "LeverRun", *a, **kw):
        super().__init__(*a, **kw)
        self.run = run

    def _arm(self, name, fn):
        self.run._phase(name)
        e0 = self.gpu.nvmlDeviceGetTotalEnergyConsumption(None)
        try:
            return fn()
        finally:
            self.run._phase("restart")   # exact arm energy from the counter, not the 1 Hz samples
            self.run._set_arm(name, (self.gpu.nvmlDeviceGetTotalEnergyConsumption(None) - e0) / 1000)

    def stock(self):
        return self._arm("without", super().stock)

    def at(self, cap=None):
        return self._arm("with", lambda: super(_PhasedCheck, self).at(cap))


class LeverRun:
    def __init__(self, gpu, docker: Docker | None = None, healthy=models_ok, asker=vllm_asker,
                 busy=lambda: None, n_prompts: int = 16, wait_s: float = 900, sample_s: float = 1.0,
                 sleep=time.sleep, log_path=None):
        """gpu: object with energy_mj() and power_w() (or None for a status-only run)."""
        self.gpu, self.docker, self.healthy, self.asker = gpu, docker or Docker(), healthy, asker
        self.busy, self.n, self.wait_s, self.sample_s, self.sleep = busy, n_prompts, wait_s, sample_s, sleep
        self.log_path = log_path
        self.thread: threading.Thread | None = None
        self._stop, self._lock = threading.Event(), threading.Lock()
        self.state: dict = {"running": False, "lever": None, "phase": "idle", "events": [], "series": [],
                            "arms": {"without": arm_totals(0), "with": arm_totals(0)}, "result": None,
                            "restore": None, "label": "MEASURED"}

    # ------------------------------------------------------------------ status helpers
    def alive(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def _log(self, **kw) -> None:
        kw["ts"] = round(time.time(), 1)
        with self._lock:
            self.state["events"] = (self.state["events"] + [kw])[-60:]
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(kw) + "\n")

    def _phase(self, p: str) -> None:
        with self._lock:
            self.state["phase"] = p

    def _set_arm(self, name: str, joules: float) -> None:
        with self._lock:
            self._acc[name] = max(0.0, joules)
            self.state["arms"][name] = arm_totals(self._acc[name])

    def stock_problems(self) -> list[str]:
        return is_stock(self.docker.argv())

    # ------------------------------------------------------------------ control
    def plan(self, lever: str) -> dict:
        if lever not in UI_LEVERS:
            raise ValueError(f"lever must be one of {list(UI_LEVERS)}")
        field, val = LEVERS[lever]
        return {"lever": lever, "change": {field: val}, "stock": dataclasses.asdict(STOCK),
                "trial_argv": glimmer_args(MODEL, dataclasses.replace(STOCK, **{field: val}))[3:],
                "stock_argv": glimmer_args(MODEL, STOCK)[3:], "prompts": self.n,
                "est_minutes": EST_MIN[lever], "est_label": "ESTIMATE", "target": URL,
                "restarts": 2, "restores_stock": True}

    def start(self, lever: str) -> dict:
        p = self.plan(lever)
        why = self.busy()
        if why:
            raise RuntimeError(why)
        if self.alive():
            raise RuntimeError("a lever trial is already running")
        if self.gpu is None:
            raise RuntimeError("no NVML on this host: a lever trial needs the GPU's own energy counter")
        if not self.healthy():
            raise RuntimeError("vLLM is not answering on :8000; start the stock server first")
        bad = self.stock_problems()
        if bad:
            raise RuntimeError(f"the running server is not stock BF16 ({'; '.join(bad)}); restore it first")
        self._stop.clear()
        self._acc = {"without": 0.0, "with": 0.0}
        with self._lock:
            self.state.update(running=True, lever=lever, phase="starting", events=[], series=[], result=None,
                              restore=None, plan=p, started_ts=time.time(), finished_ts=None, error=None,
                              arms={"without": arm_totals(0), "with": arm_totals(0)})
        self.thread = threading.Thread(target=self._run, args=(lever,), daemon=True)
        self.thread.start()
        return self.status()

    def stop(self) -> None:
        self._stop.set()
        self._log(step="stop_requested")

    def status(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.state))

    # ------------------------------------------------------------------ the trial
    def _sampler(self, done: threading.Event) -> None:
        e_last, t0, acc = self.gpu.energy_mj(), time.time(), self._acc
        while not done.is_set():
            self.sleep(self.sample_s)
            e = self.gpu.energy_mj()
            dj, e_last = (e - e_last) / 1000, e
            ph = self.state["phase"]
            try:
                w = self.gpu.power_w()
            except Exception:  # noqa: BLE001
                w = None
            with self._lock:
                if ph in acc:
                    acc[ph] += max(0.0, dj)
                    self.state["arms"][ph] = arm_totals(acc[ph])
                self.state["series"] = (self.state["series"] + [[round(time.time() - t0, 1), w, ph]])[-1800:]

    def restore(self) -> dict:
        """Put stock BF16 back if it is not already there, then verify (models + docker args)."""
        self._phase("restore")
        did = False
        if self.stock_problems() or not self.healthy():
            self.docker.restart(glimmer_args(MODEL, STOCK))
            did = True
        t0 = time.monotonic()
        while time.monotonic() - t0 < self.wait_s and not self.healthy():
            self.sleep(5)
        bad, up = self.stock_problems(), self.healthy()
        r = {"restarted": did, "models_ok": up, "args_stock": not bad, "problems": bad,
             "verified": up and not bad, "ts": round(time.time(), 1)}
        self._log(step="restore_verified" if r["verified"] else "restore_failed", **r)
        return r

    def _run(self, lever: str) -> None:
        done = threading.Event()
        smp = threading.Thread(target=self._sampler, args=(done,), daemon=True)
        smp.start()
        res, err = None, None
        try:
            prompts = [r["prompt"] for r in proof_prompts() if r.get("kind") == "math"][: self.n]
            gpu = self.gpu

            class _N:   # NVML-shaped view for AnswerCheck
                def nvmlDeviceGetTotalEnergyConsumption(self, _h):
                    return gpu.energy_mj()
            chk = _PhasedCheck(self, self.asker(stop=self._stop), _N(), prompts=prompts,
                               concurrency=16 if lever == "batching" else 1)
            res = try_lever(lever, model=MODEL, stock=STOCK, restart=self.docker.restart,
                            running_argv=self.docker.argv, healthy=self.healthy, check=chk,
                            log=self._log, wait_s=self.wait_s, sleep=self.sleep, args_fn=glimmer_args)
        except BaseException as e:  # noqa: BLE001 - Stop, gate errors, docker errors: all restore
            err = str(e)[:300]
        finally:
            try:
                rest = self.restore()
            except Exception as e:  # noqa: BLE001
                rest = {"verified": False, "problems": [f"restore error: {e}"[:300]]}
            done.set()
            smp.join(timeout=5)
            with self._lock:
                self.state.update(running=False, phase="done", result=res, error=err, restore=rest,
                                  finished_ts=time.time(), stopped=self._stop.is_set())
