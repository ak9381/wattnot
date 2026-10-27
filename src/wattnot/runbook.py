"""H100 runbook (T25): one command, staged, safe to stop at any point.

    python -m wattnot.runbook --out results/h100 [--rig fake|real] [--stages ...]

Stages, in order. Each writes into OUT and the next only runs if it passed:

  setup     nvidia-smi, NVML, power-limit permission, vLLM, model weights, disk
  probe     10-minute go/no-go: cap write + readback, energy counter moves under
            load, power stays under a lowered cap, cap restored. Exit 20 on any
            failure, so the machine can be released at once.
  sweep     baseline at the default cap, then lower caps, 5 reps each, gated
            against the baseline (quality, p99 +10%, Welch noise), per model
  headline  safe headline (T15): FP8 + 2x batch vs baseline, 5 vs 5, receipt
  best      fresh 5 vs 5 re-run of the lowest passing cap from the sweep, receipt
  ablation  cold / Spark-warm / grid Optuna, real objective, target = best seen
  budget2   2-GPU power-budget re-run (only when 2+ GPUs are visible)
  adaptive_topk  OPTIONAL, off by default: adaptive top-k on vs off on a small MoE model
            (about 25 min). Runs last. It never changes the exit code (see below).

Models run in order: Muse Glimmer 30B (default), Muse Spark Open (only on a box with
700 GB+ VRAM, e.g. 8x H200; skipped and recorded otherwise), Llama 3.1 8B (comparison).
Exit codes: 0 ok, 10 setup failed, 20 probe failed (release the GPU), 30 a later
stage failed (results so far are kept), 50 nobody approved the session (nothing was
changed). The optional adaptive_topk stage cannot change these: if it fails or is skipped the
failure is written to summary.json and the exit code stays what the other stages decided.
Receipts are signed with an offline test key only; nothing is published.

Approve before apply: a real rig changes power caps and restarts vLLM, so it first asks for
a trial grant (what it may try, within bounds; see docs/APPROVALS.md). A person approves it
(`wattnot proposals approve <id>`) or the run is started with `--pre-approved NAME`, which is
recorded in the audit trail. Every change is checked against the grant and put back at the
end. The best settings it finds are filed as pending proposals, never applied.

`--rig fake` dry-runs every stage on the simulator: all numbers are SIMULATED.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from wattnot.approvals import ApprovalGate, Approvals, NotApproved
from wattnot.approvals import proposers as _prop
from wattnot.gates.decide import decide
from wattnot.headline import (
    BASELINE,
    CANDIDATE,
    _FakeClient,
    _FakeMeter,
    gated_compare,
    measurement_label,
)
from wattnot.models import MODELS, RUN_ORDER, ServedModel, fits
from wattnot.planner.ablation import Outcome, ablation
from wattnot.planner.optuna_study import Bounds, PlannerContext, fallback_proposals, propose
from wattnot.receipt.sign import generate_test_key
from wattnot.runner.trial import run_trial
from wattnot.runner.vllm_server import OpenAIClient, TrialParams, server_args
from wattnot.runner.workload import Workload, load_replay_set
from wattnot.verify import verify_receipt

STAGES = ("setup", "probe", "sweep", "headline", "best", "ablation", "budget2")
# `flex` is opt-in (about 25 min of GPU time): it never runs unless asked for by name.
ORDER = ("setup", "probe", "flex", "sweep", "headline", "best", "ablation", "budget2",
         "adaptive_topk")
# Optional stages never decide the exit code and never stop the stages after them. Today it is the
# last one, so it also never delays a core result.
OPTIONAL = ("adaptive_topk",)
KIT_STAGES = ("setup", "probe", "flex", "sweep", "best")  # the Oct 30 H100 kit
FLEX_EVENTS = Path(__file__).resolve().parents[2] / "data" / "flex" / "h100-events.json"
EXIT_SETUP, EXIT_PROBE, EXIT_STAGE, EXIT_APPROVAL = 10, 20, 30, 50
PROBE_BUDGET_S = 600


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Plan:
    caps: tuple[int, ...] = (700, 650, 600, 550, 500, 450, 400)
    reps: int = 5
    repeat: int = 16          # tile the 8-prompt replay set so one rep is long enough to meter
    concurrency: int = 32     # requests in flight, so batching knobs matter
    ablation_trials: int = 8  # per arm
    spark: bool = False       # one Muse Spark call to seed Optuna (needs META_API_KEY)
    flex_rps: float = 8.0     # flex stage: load generator request rate
    flex_pre_s: int = 30      # flex stage: seconds measured before each event
    flex_post_s: int = 90     # flex stage: seconds of ramp-back after each event
    flex_events: str = str(FLEX_EVENTS)
    adaptive_mass: float = 0.9      # adaptive_topk stage: share of router weight to keep
    adaptive_model: str = ""        # adaptive_topk stage: MoE model ("" = the documented default)
    adaptive_budget_s: float = 1500.0
    adaptive_min_agree: float = 95.0
    models: tuple[ServedModel, ...] = RUN_ORDER
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------- rigs

class FakeRig:
    """Simulator with the same surface as RealRig. Every number it makes is SIMULATED."""

    provider = "fake"
    driver = "simulated"
    mode = "root"

    def __init__(self, *, gpus: int = 1, seed: int = 7, cap_writable: bool = True,
                 energy_counter: bool = True):
        self.gpus, self.cap_writable, self.energy_counter = gpus, cap_writable, energy_counter
        self.vram_gb = 80.0 * gpus
        self.rng = random.Random(seed)
        self.gpu = "H100 80GB (simulated)"
        self.model: ServedModel | None = None
        self.cap = 700
        self.session = None  # an approvals TrialSession, set by run()

    def cap_bounds(self) -> tuple[int, int]:
        return 200, 700

    def setup_checks(self, model: ServedModel) -> list[Check]:
        return [Check("nvidia-smi", True, "simulated"), Check("nvml", True, "simulated"),
                Check("power_limit_write", self.cap_writable, "simulated"),
                Check("vllm", True, "simulated"), Check(f"weights:{model.repo}", True, "simulated"),
                Check("disk_free_gb>=150", True, "simulated")]

    def probe(self, budget_s: float) -> tuple[bool, dict]:
        ok = self.cap_writable and self.energy_counter
        return ok, {"label": "SIMULATED", "power_cap_write": {"ok": self.cap_writable},
                    "energy_counter": self.energy_counter, "seconds": 0.0,
                    "error": None if ok else "simulated probe failure"}

    def use(self, model: ServedModel) -> None:
        self.model = model

    def set_cap(self, watts: int) -> int:
        if self.session is not None:
            self.session.check_cap(watts, 0)
        self.cap = watts
        return watts

    @contextlib.contextmanager
    def flex_session(self, progress: Path, *, rps: float):
        """Dry run: the real load generator in --fake mode writes the progress file, and the
        software power model plays the card. Everything here is SIMULATED."""
        from wattnot.flex.run import SimBackend, run_flex

        proc = _spawn_load(progress, rps=rps, fake=True, log=progress.with_suffix(".log"))
        try:
            _wait_progress(progress, 30)
            yield lambda ev, cfg: run_flex(SimBackend(seed=7), ev, cfg)
        finally:
            _stop_proc(proc)

    def _wh(self, p: TrialParams) -> float:
        cap = (p.power_cap_w or 700) / 700
        q = 0.86 if p.quantization == "fp8" else 1.0
        b = {64: 1.12, 128: 1.04, 256: 1.0, 512: 0.97}.get(p.max_num_seqs, 1.0)
        return 9.6 * q * b * (0.55 + 0.45 * cap + max(0, 0.75 - cap) ** 2) * self.gpus

    def client(self, params: TrialParams):
        self.set_cap(params.power_cap_w or 700)
        c = _FakeClient(self.rng, params)
        cap = (params.power_cap_w or 700) / 700
        slow = (1 / cap) ** 0.45

        class _Slowed:
            def complete(self, item):
                r = c.complete(item)
                return replace(r, latency_s=r.latency_s * slow, text="ok")
        return _Slowed()

    def meter(self, params: TrialParams):
        return _FakeMeter(self.rng, self._wh(params), 0.01)

    def outputs(self, items, params=None) -> dict[str, str]:
        return {i.id: "ok" for i in items}

    def idle_w(self) -> float:
        return 70.0

    def release_gpu(self) -> None:
        pass

    def close(self) -> None:
        pass


class RealRig:  # pragma: no cover - needs a GPU host
    """One host, 1 or 2 GPUs, root. vLLM runs as a subprocess (or Docker image)."""

    provider = "e2e"
    mode = "root"
    meter_name = "nvml"

    def __init__(self, out: Path, *, gpus: int = 1, port: int = 8000):
        from wattnot.probe import open_nvml
        self.out, self.gpus, self.port = out, gpus, port
        self.nvml = open_nvml()
        self.handles = [self.nvml.device_handle(i) for i in range(gpus)]
        self.gpu = self.nvml.device_name(self.handles[0])
        self.vram_gb = gpus * float(os.environ.get("WATTNOT_GPU_GB", "80"))
        self.driver = self.nvml.driver_version()
        self.saved = [self.nvml.power_limit_mw(h) for h in self.handles]
        self.model: ServedModel | None = None
        self.procs: list[subprocess.Popen] = []
        self.serving: tuple | None = None
        self.session = None  # an approvals TrialSession, set by run(); required for any change

    def cap_bounds(self) -> tuple[int, int]:
        lo, hi = self.nvml.power_limit_constraints_mw(self.handles[0])
        return lo // 1000, hi // 1000

    def _need_session(self):
        if self.session is None or self.session.mode == "dry_run":
            raise NotApproved("a real GPU rig only changes things under an approved trial "
                              "grant (see docs/APPROVALS.md); none is open")
        return self.session

    # setup
    def setup_checks(self, model: ServedModel) -> list[Check]:
        out = []
        r = _sh(["nvidia-smi", "--query-gpu=name,driver_version,power.limit,power.min_limit,"
                 "power.max_limit,memory.total", "--format=csv,noheader"])
        out.append(Check("nvidia-smi", r[0] == 0, r[1].strip()[:300]))
        out.append(Check("nvml", True, f"{self.gpu}, driver {self.driver}"))
        from wattnot.probe import probe
        lo_w, hi_w = self.cap_bounds()  # the probe writes a lower cap and puts the old one back
        for w in (lo_w, hi_w):
            self._need_session().check_cap(w, 0)
        caps = probe(self.nvml, write=True)
        pw = caps["power_cap_write"]
        out.append(Check("power_limit_write", bool(pw["ok"]), json.dumps(pw)))
        if model.vllm_image:
            r = _sh(["docker", "image", "inspect", model.vllm_image])
            if r[0] != 0:
                r = _sh(["docker", "pull", model.vllm_image], timeout=1800)
            out.append(Check("vllm", r[0] == 0, model.vllm_image))
        else:
            r = _sh([sys.executable, "-c", "import vllm;print(vllm.__version__)"])
            out.append(Check("vllm", r[0] == 0, r[1].strip()[-200:]))
        r = _sh(["hf", "download", model.repo, "--exclude", "*.pth", "original/*"],
                timeout=7200)
        out.append(Check(f"weights:{model.repo}", r[0] == 0, r[1].strip()[-300:]))
        free = shutil.disk_usage(Path.home()).free / 1e9
        out.append(Check("disk_free_gb>=150", free >= 150 or r[0] == 0, f"{free:.0f} GB free"))
        return out

    # probe: 10-minute go / no-go
    def probe(self, budget_s: float) -> tuple[bool, dict]:
        from wattnot.meter.nvml import NvmlMeter
        t0 = time.monotonic()
        info: dict = {"label": "MEASURED", "error": None}
        try:
            lo, hi = self.nvml.power_limit_constraints_mw(self.handles[0])
            low = max(lo // 1000, int(0.6 * hi // 1000))
            self.set_cap(low)
            got = self.nvml.power_limit_mw(self.handles[0]) // 1000
            info["cap_readback"] = {"asked": low, "got": got}
            if got != low:
                raise RuntimeError(f"cap readback {got} != {low}")
            self.set_cap(hi // 1000)
            self.use(self.model or RUN_ORDER[-1])  # smallest model loads fastest
            client = self.client(TrialParams(power_cap_w=low))
            wl = _tile(load_replay_set(), 4)
            meter = NvmlMeter(self.nvml, run_id="probe", traces_root=self.out / "traces")
            meter.start("probe-load")
            for it in wl:
                client.complete(it)
                if time.monotonic() - t0 > budget_s - 30:
                    break
            w = meter.stop()
            info["load_window"] = asdict(w)
            info["energy_counter"] = w.method == "counter"
            info["mean_w"] = w.mean_w
            if w.method != "counter":
                raise RuntimeError("energy counter did not move under load")
            if w.mean_w > low * 1.05 * self.gpus:
                raise RuntimeError(f"mean power {w.mean_w:.0f} W above cap {low} W")
        except Exception as e:  # noqa: BLE001 - any failure is a no-go
            info["error"] = str(e)[:500]
        finally:
            self.restore()
            info["restored"] = [self.nvml.power_limit_mw(h) for h in self.handles] == self.saved
            info["seconds"] = round(time.monotonic() - t0, 1)
        ok = info["error"] is None and info["restored"] and info["seconds"] <= budget_s
        return ok, info

    def use(self, model: ServedModel) -> None:
        self.model = model

    def set_cap(self, watts: int) -> int:
        s = self._need_session()
        spans = [self.nvml.power_limit_constraints_mw(h) for h in self.handles]
        asked = [max(lo, min(hi, watts * 1000)) // 1000 for lo, hi in spans]
        for i, w in enumerate(asked):  # check every card before writing any
            s.check_cap(w, i)
        for h, (lo, hi) in zip(self.handles, spans, strict=True):
            self.nvml.set_power_limit_mw(h, max(lo, min(hi, watts * 1000)))
        return watts

    def restore(self) -> None:
        for h, mw in zip(self.handles, self.saved, strict=True):
            self.nvml.set_power_limit_mw(h, mw)

    def _serve(self, params: TrialParams) -> None:
        key = (self.model.key, params.quantization, params.max_num_seqs,
               params.enable_prefix_caching, params.enable_chunked_prefill, params.block_size)
        if self.serving == key:
            return
        self._need_session().check_vllm({"model": self.model.key, "params": asdict(params)})
        self._stop()
        logs = self.out / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        servers = 1 if self.model.tp > 1 else self.gpus
        for g in range(servers):
            port = self.port + g
            argv = server_args(self.model.repo, replace(params, power_cap_w=None), port=port)
            argv += list(self.model.extra_args)
            if self.model.vllm_image:
                hf = Path.home() / ".cache" / "huggingface"
                argv = ["docker", "run", "--rm", "--gpus", f"device={g}", "--ipc=host",
                        "-p", f"{port}:{port}", "-v", f"{hf}:/root/.cache/huggingface",
                        "-e", "HF_TOKEN", self.model.vllm_image, *argv[2:]]
            devs = ",".join(map(str, range(self.gpus))) if self.model.tp > 1 else str(g)
            env = {**os.environ, "CUDA_VISIBLE_DEVICES": devs}
            log = (logs / f"vllm-{self.model.key}-{g}.log").open("ab")
            self.procs.append(subprocess.Popen(argv, stdout=log, stderr=log, env=env))
        for g in range(servers):
            _wait_http(f"http://127.0.0.1:{self.port + g}/v1/models", 1200)
        self.serving = key

    def _stop(self) -> None:
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            with contextlib.suppress(Exception):
                p.wait(timeout=60)
        self.procs, self.serving = [], None

    def client(self, params: TrialParams):
        self._serve(params)
        self.set_cap(params.power_cap_w or self.saved[0] // 1000)
        cs = [OpenAIClient(f"http://127.0.0.1:{self.port + g}/v1", self.model.repo,
                           chat=self.model.chat, timeout_s=300)
              for g in range(1 if self.model.tp > 1 else self.gpus)]
        if len(cs) == 1:
            return cs[0]

        class _RoundRobin:
            n = 0

            def complete(self, item):
                _RoundRobin.n += 1
                return cs[_RoundRobin.n % len(cs)].complete(item)
        return _RoundRobin()

    @contextlib.contextmanager
    def flex_session(self, progress: Path, *, rps: float):
        """vLLM up at the full cap, the load generator driving it, NVML on GPU 0.
        The load keeps running between events so p99 and pass rate are always fresh."""
        from wattnot.flex.hw import run_on_gpu

        self.client(BASELINE)  # starts the server; cap back at the saved limit
        proc = _spawn_load(progress, rps=rps, fake=False, log=self.out / "logs" / "flex-load.log",
                           url=f"http://127.0.0.1:{self.port}", model=self.model.repo)
        try:
            _wait_progress(progress, 180)
            time.sleep(60)  # let batching and clocks settle before the first baseline
            yield lambda ev, cfg: run_on_gpu(
                self.nvml, ev, progress, gpu=0, cfg=cfg, sleep=time.sleep,
                permit=lambda w: self._need_session().check_cap(w, 0))
        finally:
            _stop_proc(proc)
            self.restore()

    def meter(self, params: TrialParams):
        from wattnot.meter.nvml import NvmlMeter
        return NvmlMeter(self.nvml, run_id=f"m-{int(time.time())}",
                         traces_root=self.out / "traces")

    def outputs(self, items, params=None) -> dict[str, str]:
        c = self.client(params or BASELINE)
        return {i.id: c.complete(i).text for i in items}

    def idle_w(self) -> float:
        from wattnot.meter.nvml import NvmlMeter
        self._stop()
        m = NvmlMeter(self.nvml, run_id="idle", traces_root=self.out / "traces")
        m.start("idle")
        time.sleep(60)
        return m.stop().mean_w

    def release_gpu(self) -> None:
        """Stop vLLM so another process (the adaptive top-k stage) can use the GPU memory."""
        self._stop()

    def close(self) -> None:
        self._stop()
        self.restore()


def _sh(argv, timeout=120) -> tuple[int, str]:  # pragma: no cover - real host only
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def _wait_http(url: str, timeout_s: float) -> None:  # pragma: no cover - real host only
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        with contextlib.suppress(Exception):
            urllib.request.urlopen(url, timeout=5)  # noqa: S310 - localhost
            return
        time.sleep(5)
    raise TimeoutError(f"vLLM not up: {url}")


def _spawn_load(progress: Path, *, rps: float, fake: bool, log: Path, url: str | None = None,
                model: str | None = None) -> subprocess.Popen:
    """Start `wattnot flex load` in the background; it ends on SIGTERM."""
    progress.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, "-c", "from wattnot.cli import main; raise SystemExit(main())",
            "flex", "load", "--progress", str(progress), "--rps", f"{rps:g}"]
    argv += ["--fake"] if fake else ["--url", url or "", "--model", model or ""]
    return subprocess.Popen(argv, stdout=log.open("ab"), stderr=subprocess.STDOUT)


def _wait_progress(progress: Path, timeout_s: float) -> None:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if progress.exists() and progress.stat().st_size > 0:
            return
        time.sleep(0.5)
    raise TimeoutError(f"load generator wrote nothing to {progress} in {timeout_s:g}s")


def _stop_proc(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover - a stuck child
        proc.kill()


def _gate_cell(d) -> str:
    if d is None:
        return "baseline"
    return "pass" if d.passed else "fail:" + ",".join(d.failed)


def _tile(wl: Workload, n: int) -> Workload:
    if n <= 1:
        return wl
    items = tuple(replace(i, id=f"{i.id}#{k}") for k in range(n) for i in wl.items)
    return Workload(wl.name, wl.kind, items, wl.version)


# ---------------------------------------------------------------- stages

class Runbook:
    def __init__(self, rig, out: Path, plan: Plan, key_path: Path, run_tag: str,
                 approvals: Approvals | None = None):
        self.rig, self.out, self.plan, self.key, self.tag = rig, out, plan, key_path, run_tag
        self.approvals = approvals
        self.proposal_ids: list[str] = []
        self.summary: dict = {"run_tag": run_tag, "rig": rig.provider, "gpu": rig.gpu,
                              "plan": {k: v for k, v in asdict(plan).items() if k != "models"},
                              "models": [m.repo for m in plan.models], "stages": {}}
        self.best: dict[str, int] = {}
        fit = tuple(m for m in plan.models if fits(m, rig.vram_gb))
        self.summary["skipped_models"] = [
            {"model": m.repo, "reason": f"needs {m.min_vram_gb:g} GB VRAM, box has "
             f"{rig.vram_gb:g} GB. {m.note}"} for m in plan.models if m not in fit]
        self.plan = replace(plan, models=fit)
        self.summary["models"] = [m.repo for m in fit]

    def _wl(self) -> Workload:
        wl = load_replay_set()
        if self.rig.provider == "fake":  # the simulator answers "ok"; give it a check to pass
            wl = Workload(wl.name, wl.kind, tuple(
                replace(i, meta={**i.meta, "checks": [{"type": "contains", "value": "ok"}]})
                for i in wl.items), wl.version)
        return _tile(wl, self.plan.repeat)

    def _write(self, name: str, obj) -> Path:
        p = self.out / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, indent=2, default=str) + "\n", encoding="utf-8")
        return p

    def _rid(self, stage: str, model: ServedModel) -> str:
        h = hashlib.sha256(f"{self.tag}{stage}{model.key}".encode()).hexdigest()[:6]
        return f"run_{self.tag}_{h}"

    def setup(self) -> bool:
        checks = [asdict(c) for m in self.plan.models for c in self.rig.setup_checks(m)]
        self._write("setup.json", checks)
        return all(c["ok"] for c in checks)

    def probe(self) -> bool:
        self.rig.use(self.plan.models[-1])  # the smallest model in the plan loads fastest
        ok, info = self.rig.probe(PROBE_BUDGET_S)
        self._write("probe.json", {"ok": ok, **info})
        return ok

    def flex(self) -> bool:
        """Shed test: three grid asks while the load generator keeps the server busy.
        Each event is 30 s before + 300 s event + 90 s ramp-back (about 7 min), so the
        stage is about 21 min of event time plus warm-up. One signed flex receipt each."""
        from wattnot.flex import signal as fsig
        from wattnot.flex.controller import FlexConfig
        from wattnot.flex.receipt import check_trace, write_flex_receipt

        sig = fsig.load(self.plan.flex_events)
        self.rig.use(self.plan.models[0])
        cfg = FlexConfig(pre_s=self.plan.flex_pre_s, post_s=self.plan.flex_post_s)
        fdir = self.out / "flex"
        rows, prev = [], None
        with self.rig.flex_session(fdir / "progress.jsonl", rps=self.plan.flex_rps) as run_event:
            for ev in sig.events:
                self._check_flex(ev)
                fr = run_event(ev, cfg)
                w = write_flex_receipt(fr, sig, fdir, prev_sha256=prev, sign_key=str(self.key))
                r = w["receipt"]
                prev = r["integrity"]["sha256"]
                rep = verify_receipt(Path(w["path"]))
                trace_ok, problems = check_trace(w["path"])
                o = r["flex"]["outcome"]
                rows.append({"event": ev.id, "target_pct": o["target_pct"],
                             "delivered_pct": o["delivered_pct"], "status": o["status"],
                             "limit": o["limit"], "p99_ms": o["p99_ms"],
                             "label": r["flex"]["label"],
                             "grade": rep.grade, "trace_ok": trace_ok, "problems": problems,
                             "verify_ok": all(st.ok for st in rep.steps if st.name != "rekor"),
                             "receipt": str(Path(w["path"]).relative_to(self.out))})
        self.summary["flex"] = rows
        return all(x["verify_ok"] and x["trace_ok"] for x in rows)

    def sweep(self) -> bool:
        rows = []
        for m in self.plan.models:
            self.rig.use(m)
            wl, items = self._wl(), None
            items = list(wl)
            base_p = replace(BASELINE, power_cap_w=self.plan.caps[0])
            res = {}
            for cap in self.plan.caps:
                p = replace(BASELINE, power_cap_w=cap)
                res[cap] = run_trial(p, wl, self.rig.meter(p), self.rig.client(p),
                                     reps=self.plan.reps, trial_id=f"{m.key}-cap{cap}",
                                     warmup=min(20, len(items)),
                                     concurrency=self.plan.concurrency)
            outs = self.rig.outputs(items, base_p)
            base = res[self.plan.caps[0]]
            bwh = [r.wh_per_request for r in base.reps]
            for cap, t in res.items():
                twh = [r.wh_per_request for r in t.reps]
                d = None if cap == self.plan.caps[0] else decide(
                    base, t, outs, outs, items, base_wh=bwh, cand_wh=twh)
                rows.append({"model": m.repo, "cap_w": cap,
                             "wh_per_req": round(t.wh_per_request, 7),
                             "wh_per_1k_tok": round(t.wh_per_1k_tokens, 6),
                             "p99_s": round(t.p99_s, 4),
                             "label": measurement_label(self.rig, [r.window for r in t.reps]),
                             "gate": _gate_cell(d)})
                if d is not None and d.passed:
                    self.best[m.key] = min(self.best.get(m.key, cap), cap)
        with (self.out / "sweep.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        self.summary["best_cap_w"] = dict(self.best)
        return True

    def _propose(self, receipt_path: Path, decision, model: ServedModel) -> list[str]:
        """File the settings this receipt backs as pending proposals. Never applies anything,
        and never changes a stage result: a failure here is only recorded."""
        if self.approvals is None:
            return []
        try:
            receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
            specs = _prop.from_gpu_receipt(
                receipt, gates=getattr(decision, "results", ()),
                source={"stage_model": model.repo, "run_tag": self.tag}, ttl_s=7 * 86400)
            ids = []
            if decision is not None and not decision.passed:
                return []  # a candidate that failed its gates is never proposed
            for spec in specs:
                p = self.approvals.propose(spec)
                ids.append(p["id"])
            self.proposal_ids += ids
            return ids
        except Exception as e:  # noqa: BLE001 - proposals are extra; the run is the point
            self.summary.setdefault("proposal_errors", []).append(f"{type(e).__name__}: {e}"[:300])
            return []

    def _check_flex(self, ev) -> None:
        s = getattr(self.rig, "session", None)
        if s is not None:
            s.check_flex(ev.target_pct, ev.duration_s)

    def _compare(self, stage, m, base, cand, reason) -> dict:
        self.rig.use(m)
        r = gated_compare(self.rig, baseline=base, candidate=cand, root=self.out,
                          key_path=self.key, run_id=self._rid(stage, m), reps=self.plan.reps,
                          workload=self._wl(), reason=reason,
                          concurrency=self.plan.concurrency)
        rep = verify_receipt(r.receipt)
        proposals = self._propose(r.receipt, r.decision, m)
        return {"model": m.repo, "receipt": str(r.receipt.relative_to(self.out)),
                "passed": r.decision_passed,
                "verify_ok": all(st.ok for st in rep.steps if st.name != "rekor"),
                "grade": rep.grade,  # rekor: offline test key, never published
                "proposals": proposals}

    def headline(self) -> bool:
        self.summary["headline"] = [self._compare(
            "headline", m, BASELINE, CANDIDATE,
            "safe headline: FP8 weights plus 2x batch, no power cap") for m in self.plan.models]
        return all(x["verify_ok"] for x in self.summary["headline"])

    def best_rerun(self) -> bool:
        out = []
        for m in self.plan.models:
            cap = self.best.get(m.key)
            if cap is None:
                out.append({"model": m.repo, "skipped": "no cap passed the gate in the sweep"})
                continue
            out.append(self._compare("best", m, replace(BASELINE, power_cap_w=self.plan.caps[0]),
                                     replace(BASELINE, power_cap_w=cap),
                                     f"best-point re-run: lowest passing cap, {cap} W"))
        self.summary["best"] = out
        return all(x.get("verify_ok", True) for x in out)

    def ablation_stage(self) -> bool:
        m = self.plan.models[0]
        self.rig.use(m)
        b = Bounds(cap_min_w=min(self.plan.caps), cap_max_w=max(self.plan.caps), cap_step_w=50,
                   max_num_seqs=(128, 256, 512))
        wl = _tile(self._wl(), max(1, 4 // max(1, self.plan.repeat)))
        cache: dict = {}

        def objective(p: dict) -> Outcome:
            k = tuple(sorted(p.items()))
            if k not in cache:
                tp = TrialParams(max_num_seqs=p["max_num_seqs"], quantization=p["quantization"],
                                 power_cap_w=p["power_cap_w"])
                t0 = time.monotonic()
                t = run_trial(tp, wl, self.rig.meter(tp), self.rig.client(tp), reps=2,
                              warmup=8, concurrency=self.plan.concurrency)
                cache[k] = Outcome(t.wh_per_request, t.p99_s, time.monotonic() - t0,
                                   sum(r.window.wh for r in t.reps))
            return cache[k]

        base = objective({"power_cap_w": b.cap_max_w, "max_num_seqs": 256, "quantization": "none"})
        ctx = PlannerContext(b, base.wh_per_req, base.p99_s, base.p99_s * 1.10)
        if self.plan.spark and os.environ.get("META_API_KEY"):
            props, usage = propose(ctx, n=4)
            spark = {"calls": 1, **usage}
        else:
            props, spark = fallback_proposals(ctx, 4), {"calls": 0, "note": "fixed spread"}
        rep = ablation(self.plan.ablation_trials, proposals=props, spark=spark,
                       objective=objective, bounds=b, p99_budget_s=ctx.p99_budget_s,
                       target_mode="best_seen",
                       label="SIMULATED" if self.rig.provider == "fake" else "MEASURED")
        self._write("ablation.json", {"model": m.repo, **rep.to_dict(),
                                      "unique_trials_run": len(cache)})
        return True

    def budget2(self) -> bool:
        if self.rig.gpus < 2:
            self.summary["budget2"] = {"skipped": "needs 2 GPUs (book 2x H100 for this stage)"}
            return True
        m = self.plan.models[0]
        cap = self.best.get(m.key, self.plan.caps[len(self.plan.caps) // 2])
        r = self._compare("budget2", m, replace(BASELINE, power_cap_w=self.plan.caps[0]),
                          replace(BASELINE, power_cap_w=cap),
                          f"2-GPU power budget: both GPUs at {cap} W vs {self.plan.caps[0]} W")
        r["budget_w"] = {"before": 2 * self.plan.caps[0], "after": 2 * cap,
                         "freed_w": 2 * (self.plan.caps[0] - cap)}
        self.summary["budget2"] = r
        return r["verify_ok"]

    def adaptive_topk(self) -> bool:
        """OPTIONAL: adaptive top-k vs normal top-k on a small MoE model, same load, joules per
        token from the NVML energy counter. Runs under the trial grant, writes a signed receipt
        and, only if the quality, p99 and energy gates all passed, files a PENDING `moe_routing`
        proposal (nothing is switched on). Raises on any failure; `run` records it and moves on."""
        from wattnot.moe import adaptive_topk as at

        cfg = at.AdaptiveConfig(
            model=self.plan.adaptive_model or at.DEFAULT_MODEL, mass=self.plan.adaptive_mass,
            budget_s=self.plan.adaptive_budget_s, min_agreement_pct=self.plan.adaptive_min_agree)
        backend = _adaptive_backend(self.rig, cfg)
        res = at.run_trial(backend, cfg, session=getattr(self.rig, "session", None))
        out = self.out / "adaptive_topk"
        s = at.summarize(res)
        self.summary["adaptive_topk"] = info = {
            "label": s["label"], "model": cfg.model, "mass": cfg.mass,
            "saving_pct_j_per_token": round(s["saving_pct"], 2),
            "agreement_pct": round(float(res.agreement["pct"]), 2),
            "p99_increase_pct": round(s["p99_increase_pct"], 2),
            "reps": {"off": s["off"]["reps"], "on": s["on"]["reps"]},
            "gates": s["gates"], "truncated": res.truncated, "seconds": round(res.seconds, 1),
            "known_technique": True, "prior_art": list(at.PRIOR_ART)}
        approval = None
        sess = getattr(self.rig, "session", None)
        if self.approvals is not None and sess is not None and sess.id:
            approval = self.approvals.receipt_block(sess.id)
        w = at.write_receipt(res, out, sign_key=str(self.key), approval=approval)
        rep = verify_receipt(Path(w["path"]))
        info["receipt"] = str(Path(w["path"]).relative_to(self.out))
        info["verify_ok"] = all(st.ok for st in rep.steps if st.name != "rekor")
        info["grade"] = rep.grade
        if s["all_gates_passed"] and self.approvals is not None:
            from wattnot.approvals.proposers import from_moe_receipt

            p = self.approvals.propose(from_moe_receipt(
                w["receipt"], source={"run_tag": self.tag}, ttl_s=7 * 86400))
            self.proposal_ids.append(p["id"])
            info["proposal"] = p["id"]
        else:
            info["proposal"] = None
            info["not_proposed"] = [k for k, g in s["gates"].items() if not g["passed"]] or \
                ["no approvals store"]
        return info["verify_ok"]


def _adaptive_backend(rig, cfg):
    """The backend for the adaptive top-k stage: a simulator on the fake rig, transformers on a
    real one."""
    from wattnot.moe.adaptive_topk import FakeMoeBackend

    if rig.provider == "fake":
        return FakeMoeBackend(fail=getattr(rig, "adaptive_fail", None))
    from wattnot.moe.hf_backend import HFBackend  # pragma: no cover - needs a GPU host

    return HFBackend(cfg, rig)  # pragma: no cover


def _session_bounds(rig, plan: Plan, stages) -> dict:
    """What a run may try: caps within the hardware range, vLLM restarts, the flex events and,
    only when the adaptive top-k stage is in the run, that one trial."""
    lo, hi = rig.cap_bounds()
    b: dict = {"power_cap_w": {"min": lo, "max": hi}, "gpus": list(range(rig.gpus)),
               "vllm_config": True}
    if "flex" in stages:
        from wattnot.flex import signal as fsig

        evs = fsig.load(plan.flex_events).events
        b["flex_shed"] = {"max_target_pct": max(e.target_pct for e in evs),
                          "max_duration_s": max(e.duration_s for e in evs)}
    if "adaptive_topk" in stages:
        from wattnot.moe.adaptive_topk import MAX_MASS, MIN_MASS

        b["moe_routing"] = {"min_mass": MIN_MASS, "max_mass": MAX_MASS}
    return b


def _export_approvals(store, out: Path, ids: list[str]) -> None:
    """Copy the audit trail and the proposals this run touched into the results folder."""
    d = out / "approvals"
    d.mkdir(parents=True, exist_ok=True)
    if store.audit_path.exists():
        shutil.copy2(store.audit_path, d / "audit.jsonl")
    for pid in ids:
        src = store._path(pid)
        if src.exists():
            shutil.copy2(src, d / src.name)


def run(rig, out: str | Path, *, plan: Plan | None = None, stages=STAGES,
        key_path: str | Path | None = None, run_tag: str | None = None, pack: bool = True,
        gate: ApprovalGate | None = None) -> int:
    """Run the stages. `gate` decides whether a real rig may change anything (see
    docs/APPROVALS.md). A fake rig gets a throwaway dry-run gate if none is given."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    plan = plan or Plan()
    tag = run_tag or datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    key = Path(key_path) if key_path else Path.home() / ".wattnot" / "test-key.pem"
    if not key.exists():
        key.parent.mkdir(parents=True, exist_ok=True)
        generate_test_key(key)  # offline test key, kept OUT of the results folder
    scratch = None
    if gate is None and rig.provider == "fake":
        import tempfile

        scratch = tempfile.mkdtemp(prefix="wattnot-dryrun-")
        gate = ApprovalGate(Approvals(scratch), mode="dry_run", announce=lambda _m: None)
    if gate is None:
        print("no approval gate was given: a real rig changes nothing without one "
              "(see docs/APPROVALS.md)", flush=True)
        return EXIT_APPROVAL
    rb = Runbook(rig, out, plan, key, tag, approvals=gate.approvals)
    steps = {"setup": (rb.setup, EXIT_SETUP), "probe": (rb.probe, EXIT_PROBE),
             "flex": (rb.flex, EXIT_STAGE),
             "sweep": (rb.sweep, EXIT_STAGE), "headline": (rb.headline, EXIT_STAGE),
             "best": (rb.best_rerun, EXIT_STAGE), "ablation": (rb.ablation_stage, EXIT_STAGE),
             "budget2": (rb.budget2, EXIT_STAGE), "adaptive_topk": (rb.adaptive_topk, EXIT_STAGE)}
    code = 0
    session = gate.session(
        title=f"WattNot run {tag}: try power caps and vLLM settings on {rig.gpu}",
        bounds=_session_bounds(rig, plan, stages),
        source={"tool": "runbook", "run_tag": tag, "stages": list(stages), "rig": rig.provider},
        ttl_s=8 * 3600,
        reason="measure each setting against the baseline; everything is restored afterwards")
    try:
        session.__enter__()
    except NotApproved as e:
        print(f"not approved: {e}", flush=True)
        rig.close()
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
        return EXIT_APPROVAL
    rig.session = session
    rb.summary["approvals"] = {"mode": gate.mode, "grant": session.id}
    try:
        for name in ORDER:
            if name not in stages:
                continue
            t0 = time.monotonic()
            try:
                ok = steps[name][0]()
                err = None
            except Exception as e:  # noqa: BLE001 - keep results, report, stop
                ok, err = False, f"{type(e).__name__}: {e}"[:500]
            rb.summary["stages"][name] = {"ok": ok, "seconds": round(time.monotonic() - t0, 1),
                                          **({"error": err} if err else {})}
            print(f"[{name}] {'ok' if ok else 'FAILED'}" + (f": {err}" if err else ""),
                  flush=True)
            if not ok and name in OPTIONAL:
                rb.summary["stages"][name]["optional"] = True
                print(f"[{name}] is optional: the rest of the run and the exit code are "
                      "unaffected", flush=True)
                continue
            if not ok:
                code = steps[name][1]
                if name == "probe":
                    print("probe failed: release the machine now (no further GPU time).")
                break
    finally:
        rig.close()  # puts every cap back first; only then does the grant close
        rig.session = None
        session.__exit__(None, None, None)
        g = gate.approvals.get(session.id)
        rb.summary["approvals"].update(
            status=g["status"], approved_by=(g.get("decision") or {}).get("by"),
            usage=(g.get("grant") or {}).get("usage"),
            proposals=list(rb.proposal_ids),
            audit_chain_problems=gate.approvals.verify())
        _export_approvals(gate.approvals.store, out, [session.id, *rb.proposal_ids])
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
        rb.summary["receipt_labels"] = _receipt_labels(out)
        print("receipts by label:", json.dumps(rb.summary["receipt_labels"]), flush=True)
        rb._write("summary.json", rb.summary)
        _manifest(out)
        if pack:
            with tarfile.open(out.with_suffix(".tar.gz"), "w:gz") as tf:
                tf.add(out, arcname=out.name)
    return code


def _receipt_labels(out: Path) -> dict[str, int]:
    """Count the receipts in `out` by their measurement label (MEASURED / SIMULATED / ...)."""
    n: dict[str, int] = {}
    for p in sorted(out.rglob("rcpt_*.json")):
        if p.name.endswith((".sigstore.json", ".trace.json")):
            continue
        try:
            lab = json.loads(p.read_text(encoding="utf-8"))["measurement"]["label"]
        except (OSError, ValueError, KeyError, TypeError):
            lab = "UNREADABLE"
        n[lab] = n.get(lab, 0) + 1
    return n


def _manifest(out: Path) -> None:
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(out)}"
             for p in sorted(out.rglob("*")) if p.is_file() and p.name != "MANIFEST.sha256"]
    (out / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m wattnot.runbook",
                                 description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="results/h100")
    ap.add_argument("--rig", choices=("fake", "real"), default="fake")
    ap.add_argument("--gpus", type=int, default=1)
    ap.add_argument("--gpu-gb", type=float, default=80.0, help="VRAM per GPU (141 for H200)")
    ap.add_argument("--stages", default=",".join(STAGES))
    ap.add_argument("--spark", action="store_true", help="one Muse Spark call seeds Optuna")
    ap.add_argument("--quick", action="store_true", help="tiny plan for a dry run")
    ap.add_argument("--flex-rps", type=float, default=8.0,
                    help="flex stage: load generator requests per second")
    ap.add_argument("--models", default="",
                    help="comma list of model keys to serve, in order (default: all that fit). "
                         "Example without an HF token: muse-glimmer,qwen2.5-7b")
    ap.add_argument("--fail-probe", action="store_true", help="fake rig: simulate a no-go")
    ap.add_argument("--adaptive-topk", action="store_true",
                    help="also run the OPTIONAL adaptive top-k trial after the other stages "
                         "(off by default; about 25 min of GPU time, see docs/ADAPTIVE-TOPK.md)")
    ap.add_argument("--adaptive-mass", type=float, default=0.9,
                    help="adaptive top-k: share of router weight to keep per token (default 0.9)")
    ap.add_argument("--adaptive-model", default="",
                    help="adaptive top-k: MoE model repo (default: the documented Granite model)")
    ap.add_argument("--adaptive-budget-s", type=float, default=1500.0,
                    help="adaptive top-k: time box for the whole stage (default 1500 s)")
    ap.add_argument("--adaptive-min-agree", type=float, default=95.0,
                    help="adaptive top-k: quality gate, token agreement with normal top-k (%%)")
    ap.add_argument("--adaptive-fail", choices=("prepare", "generate"), default=None,
                    help="fake rig: make the adaptive trial fail, to check the rest still passes")
    ap.add_argument("--pre-approved", metavar="NAME", default=None,
                    help="a person approved this whole session in advance; NAME goes in the audit "
                         "trail. Without it a real rig waits for `wattnot proposals approve`")
    ap.add_argument("--approval-wait-s", type=float, default=600.0,
                    help="how long to wait for an approval before exiting 50 (default 600)")
    ap.add_argument("--proposals-dir", default=None,
                    help="where proposals and the audit trail live "
                         "(default $WATTNOT_PROPOSALS_DIR or ./proposals)")
    a = ap.parse_args(argv)
    if "adaptive_topk" in a.stages or a.adaptive_topk:
        from wattnot.moe.adaptive_topk import MAX_MASS, MIN_MASS

        if not MIN_MASS <= a.adaptive_mass <= MAX_MASS:
            ap.error(f"--adaptive-mass must be between {MIN_MASS} and {MAX_MASS}")
    stages = tuple(a.stages.split(","))
    if a.adaptive_topk and "adaptive_topk" not in stages:
        stages += ("adaptive_topk",)
    plan = Plan(spark=a.spark, flex_rps=a.flex_rps, adaptive_mass=a.adaptive_mass,
                adaptive_model=a.adaptive_model, adaptive_budget_s=a.adaptive_budget_s,
                adaptive_min_agree=a.adaptive_min_agree)
    if a.models:
        keys = [k.strip() for k in a.models.split(",") if k.strip()]
        bad = [k for k in keys if k not in MODELS]
        if bad:
            ap.error(f"unknown model key(s) {bad}; choose from {sorted(MODELS)}")
        plan = replace(plan, models=tuple(MODELS[k] for k in keys))
    if a.quick:
        plan = replace(plan, caps=(700, 600, 500), reps=5, repeat=1, concurrency=1,
                       ablation_trials=4)
    out = Path(a.out)
    rig = (FakeRig(gpus=a.gpus, cap_writable=not a.fail_probe) if a.rig == "fake"
           else RealRig(out, gpus=a.gpus))
    rig.vram_gb = a.gpus * a.gpu_gb
    gate = None
    if a.rig == "real" or a.pre_approved or a.proposals_dir:
        apps = Approvals(a.proposals_dir)
        if a.rig == "real":
            from wattnot.approvals.appliers import nvml_appliers

            apps.appliers.update(nvml_appliers(rig.nvml))
        gate = ApprovalGate(
            apps, mode="pre_approved" if a.pre_approved
            else ("dry_run" if a.rig == "fake" else "ask"),
            name=a.pre_approved or "", wait_s=a.approval_wait_s)
        if a.pre_approved:
            print(f"PRE-APPROVED by flag ({a.pre_approved}): this run may change power caps "
                  "and restart vLLM inside the bounds in its grant. Recorded in the audit "
                  "trail.", flush=True)
    rig.adaptive_fail = a.adaptive_fail  # only the fake backend looks at this
    return run(rig, out, plan=plan, stages=stages, gate=gate)


if __name__ == "__main__":
    sys.exit(main())
