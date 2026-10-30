"""The long-run driver: runs the block plan in order, records each block, never loses results.

    python -m wattnot.longrun.driver --dry-run --out /tmp/longrun-dry      # CPU + mocks, no VM
    python -m wattnot.longrun.driver --live --model glimmer-test --host IP  # real VM, real numbers

Exactly one of --dry-run / --live is required. The dry run uses the scripted `FakeVm` and the
blocks' `--fake` commands (labelled MOCK/SIMULATED); nothing is measured and no VM is touched.
The live run uses a real VM (already launched by scripts/e2e/ctl.py) and runs each block's REAL
command — never `--fake` — so every number is MEASURED on the H100 / Muse Glimmer. A live run
first calls the no-mock preflight (wattnot.longrun.nomock) and refuses to start if any live input
or step is a mock, placeholder, stub or replay.

Per block: dependency + money + time check -> recorder.begin -> run (bounded by the block's budget
and the deadline) -> (link lost? -> plan_teardown) -> pull -> recorder.end -> checkpoint. Blocks
are dropped from the bottom when the time left cannot cover them, and a block whose dependency did
not finish is skipped. A restarted driver (--resume via the saved state) skips blocks already done.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable, Protocol

from wattnot.proof.teardown import plan_teardown

from .recorder import Recorder

INR_PER_H = 451.2          # E2E H100 + GST + disk (MEASURED price list)
INR_PER_USD = 88.0         # ASSUMED
BALANCE_FLOOR_INR = 1500.0  # the balance watchdog floor in long-run-plan.md
LINK_LOST_RCS = (4, 5)     # run2_remote: 4 = link lost mid-setup, 5 = unreachable
WATCHDOG_MARGIN_H = 8 / 60.0  # ctl.py deletes the VM at deadline minus 8 min: stop before then
OVERRUN_FACTOR = 1.5       # a block may run up to 1.5x its budget before it is killed

# Remote layout a live run drives (matches scripts/run2_remote.py and h100_vm_setup.sh).
REMOTE_REPO = "/root/wattnot"
REMOTE_RESULTS = "/root/run2/results"
REPO_ROOT = Path(__file__).resolve().parents[3]
RUN2_REMOTE = REPO_ROOT / "scripts" / "run2_remote.py"
CTL = REPO_ROOT / "scripts" / "e2e" / "ctl.py"
GLIMMER_MODEL = "meta-models/Muse-Glimmer-30B"


@dataclass
class Block:
    id: str
    title: str
    hours: float
    dry: list[str] | None          # argv for the dry run (--fake); None = nothing to simulate
    live: list[str] | None = None  # argv for the live run (REAL commands, never --fake)
    capture: bool = False
    optional: bool = False         # needs Akash's yes (e.g. the second model)
    min_hours: float | None = None  # a trimmed version fits in this much time ...
    trim: list[str] | None = None   # ... with this dry argv (e.g. fewer items per tier)
    trim_live: list[str] | None = None  # ... and this live argv
    depends_on: tuple[str, ...] = ()  # skip this block unless every dep finished 'done'
    host: bool = False             # live: run `live` on the GPU host over SSH (else on control)
    infra: str = ""                # live: a driver-run infra step: "setup" | "pull"


def _py(*args: str) -> list[str]:
    """A control-machine command (this interpreter)."""
    return [sys.executable, "-m", *args]


def _rpy(*args: str) -> list[str]:
    """A GPU-host command, run under the remote venv (`python` after activate)."""
    return ["python", "-m", *args]


def _m(minutes: float) -> float:
    return minutes / 60.0


def plan(out: Path, model: str = "llama") -> list[Block]:  # see plan_glimmer for the primary
    """The block list. model="llama" is plan A (Llama 8B main, Glimmer optional);
    model="glimmer" is plan B (Glimmer 30B main; Llama RUN2 stays as prior evidence)."""
    if model == "glimmer":
        return plan_glimmer(out)
    if model == "glimmer-test":
        return plan_glimmer_test(out)
    live = out / "live.jsonl"
    reh = lambda tag, *extra: _py("wattnot.proof.orchestrate", "--mode", "rehearsal",  # noqa: E731
                                  "--out", str(out / "runs" / tag), "--profile", "quick",
                                  "--auto-approve", "--fresh", "--live-file", str(live), *extra)
    guard = str(Path(__file__).resolve().parents[3] / "examples/longrun/guardrails-wonder.json")
    return [
        Block("b0-setup", "Launch, setup, probe, go/no-go", 1.0, None),
        Block("b1-baseline-350", "Baseline + 350 W x3 repeats (with the 325 W rejection)", 1.75,
              reh("b1", "--no-adaptive")),
        Block("b2-wonder-sweep", "Wonder-supervised sweep 300-375 W + clock locks", 2.0,
              reh("b2", "--planner", "wonder", "--max-proposals", "10", "--no-adaptive",
                  "--guardrail-config", guard)),
        Block("b3-tiers", "Tiered prompt set at the best cap", 2.0,
              _py("wattnot.longrun.harness", "--cap", "350", "--out", str(out / "runs" / "b3"),
                  "--fake")),
        Block("b4-camera", "Camera-ready run in one take", 1.0, reh("b4"), capture=True),
        Block("b5-second-model", "Second model (Glimmer 30B)", 2.5, None, optional=True),
        Block("b6-flex", "Flex / load-shed test", 0.75, None),
        # b7 (adaptive top-k) dropped Oct 29: Meta-only rule; top-k is planned on Spark via Modal.
        Block("b8-spark-open", "Spark Open 816B on one GPU", 3.5, None),
        Block("b9-buffer", "Buffer for redoing failed blocks", 1.5, None),
    ]


def plan_glimmer(out: Path) -> list[Block]:
    """Plan B, the primary: Muse Glimmer 30B (Meta) main, one VM session, no relaunch."""
    live = out / "live.jsonl"
    reh = lambda tag, *extra: _py("wattnot.proof.orchestrate", "--mode", "rehearsal",  # noqa: E731
                                  "--out", str(out / "runs" / tag), "--profile", "quick",
                                  "--auto-approve", "--fresh", "--live-file", str(live), *extra)
    guard = str(Path(__file__).resolve().parents[3] / "examples/longrun/guardrails-wonder.json")
    tiers = lambda tag, cfg, *x: _py("wattnot.longrun.harness", "--cap", "350",  # noqa: E731
                                     "--out", str(out / "runs" / tag), "--fake",
                                     "--model-config", cfg, *x)
    full = str(out / "runs" / "g3" / "tiers_summary.json")
    return [
        Block("g0-setup", "Launch, setup, Glimmer weights (55.5 GiB), probe", 1.0, None),
        # same VM session, no relaunch (E2E's 24 h balance rule allows one launch)
        Block("g0b-shakedown", "Glimmer shakedown: memory headroom, FP8 start, timings, thinking "
              "budget, harness fixes (not a measured result)", 2.0,
              tiers("g0b", "muse-glimmer-30b", "--per-tier", "4")),
        # safety first: one baseline/best-cap pair saved with its receipt before any repeat
        Block("g1a-pair", "1 + 1: baseline and best cap, one pair, checkpoint + receipt", 0.75,
              reh("g1a", "--no-adaptive")),
        Block("g1b-repeats", "Two more baseline/best-cap repeats (3x total) + 325 W rejection",
              1.75, reh("g1b", "--no-adaptive")),
        Block("g2-wonder-sweep", "Wonder-supervised sweep 300-375 W + clock locks", 2.75,
              reh("g2", "--planner", "wonder", "--max-proposals", "10", "--no-adaptive",
                  "--guardrail-config", guard), min_hours=1.5,
              trim=reh("g2", "--planner", "wonder", "--max-proposals", "5", "--no-adaptive",
                       "--guardrail-config", guard)),
        Block("g3-tiers", "Tiered prompt set on Glimmer, stock vs best cap", 3.0,
              tiers("g3", "muse-glimmer-30b"), min_hours=1.5,
              trim=tiers("g3", "muse-glimmer-30b", "--per-tier", "8")),
        Block("g4-camera", "Camera-ready run in one take (Glimmer)", 1.25, reh("g4"),
              capture=True),
        Block("g5-fp8", "FP8 vs BF16, gated keep/reject on the same tiers", 1.5,
              tiers("g5", "muse-glimmer-30b-fp8", "--compare-to", full), min_hours=0.75,
              trim=tiers("g5", "muse-glimmer-30b-fp8", "--per-tier", "8", "--compare-to", full)),
        # Adaptive top-k is not in the H100 run: Glimmer is dense, and top-k is planned on
        # Spark via Modal. In its place: the thinking budget per tier and prefix caching.
        Block("g6-think", "Thinking budget per tier: 11 cells, stop-and-force-answer, "
              "J/correct gate (1 pt), thinking_policy.json", 0.75,
              _py("wattnot.longrun.thinking_budget", "--fake", "--out",
                  str(out / "runs" / "g6")), min_hours=0.5,
              trim=_py("wattnot.longrun.thinking_budget", "--fake", "--per-tier", "8", "--out",
                       str(out / "runs" / "g6"))),
        # Prefix caching off vs on lives in the long run, not the 2 h test (moved out to fit).
        Block("g6b-prefix", "Prefix caching off vs on, Complex tier (ABBA, n>=3)", 0.42,
              [sys.executable, str(Path(__file__).resolve().parents[3] / "scripts" /
                                   "prefix_cache_ab.py"), "--fake", "--out",
               str(out / "runs" / "g6b")]),
        Block("g7-flex", "Flex / load-shed test", 0.75, None),
        Block("g8-buffer", "Buffer for redoing failed blocks (15 min went to g6)", 0.75, None),
    ]


# Vision eye-test wall-clock budget inside the 2 h Glimmer test. The pre-generated
# SYNTHETIC images are cached, so answering them is seconds of served inference, not
# the 13 s hot-path generation; 90 s is a comfortable cap per candidate.
EYE_TEST_BUDGET_S = 90.0

# Watts-per-spoken-answer wall-clock budget inside the 2 h Glimmer test. The 20
# recordings are transcribed once (cached), so answering them is just seconds of
# served inference; 90 s is a comfortable cap per candidate.
SPOKEN_TEST_BUDGET_S = 90.0

# The served Glimmer endpoint on the GPU host (eye/spoken/harness/probe all run there).
HOST_BASE_URL = "http://127.0.0.1:8000/v1"


def plan_glimmer_test(out: Path) -> list[Block]:
    """The 2 h Glimmer test VM, GPU-first so the core proof is saved before anything optional.

    Order: setup -> probe (cap write + image acceptance) -> BF16 power-cap sweep + signed receipt
    -> one forced-SSH-drop resume test -> eye + spoken on BF16 -> FP8 swap -> eye + spoken on FP8
    -> copy results back. Two prompts per tier. Budgeted to 100-110 min of the ~112 usable (2 h
    minus the 8 min watchdog margin). The prefix-cache A/B is NOT here; it is in the long run.

    Every block carries both a dry argv (--fake, MOCK/SIMULATED) and a live argv (the real command,
    never --fake). Live `host=True` blocks run on the GPU host over SSH against the served model.
    """
    live_file = out / "live.jsonl"

    def tiers_dry(tag, cfg):
        return _py("wattnot.longrun.harness", "--cap", "350", "--out", str(out / "runs" / tag),
                   "--fake", "--model-config", cfg, "--per-tier", "2")

    def tiers_live(tag, cfg):
        return _rpy("wattnot.longrun.harness", "--cap", "350", "--out", f"{REMOTE_RESULTS}/{tag}",
                    "--model-config", cfg, "--per-tier", "2",
                    "--url", f"{HOST_BASE_URL}/chat/completions")

    def eye_dry(tag):
        return _py("wattnot.vision.run", "--fake", "--out", str(out / "runs" / tag),
                   "--budget-s", str(EYE_TEST_BUDGET_S))

    def eye_live(tag):
        return _rpy("wattnot.vision.run", "--base-url", HOST_BASE_URL,
                    "--out", f"{REMOTE_RESULTS}/{tag}", "--budget-s", str(EYE_TEST_BUDGET_S))

    def spoken_dry(tag):
        return _py("wattnot.spokenanswer.run", "--fake", "--out", str(out / "runs" / tag),
                   "--budget-s", str(SPOKEN_TEST_BUDGET_S))

    def spoken_live(tag):
        return _rpy("wattnot.spokenanswer.run", "--base-url", HOST_BASE_URL,
                    "--out", f"{REMOTE_RESULTS}/{tag}", "--budget-s", str(SPOKEN_TEST_BUDGET_S))

    sweep_dry = _py("wattnot.proof.orchestrate", "--mode", "rehearsal", "--out",
                    str(out / "runs" / "t1-sweep"), "--profile", "quick", "--planner", "wonder",
                    "--no-adaptive", "--auto-approve", "--fresh", "--live-file", str(live_file))
    sweep_live = _py("wattnot.proof.orchestrate", "--mode", "live", "--host", "{HOST}",
                     "--key", "{KEY}", "--model", GLIMMER_MODEL, "--out", str(out / "runs" / "t1-sweep"),
                     "--profile", "quick", "--planner", "wonder", "--no-adaptive",
                     "--sweep-budget-s", "1100", "--sign-key", "receipt-offline-key.pem",
                     "--auto-approve", "--live-file", str(live_file))

    return [
        Block("t0-setup", "Launch, setup, Glimmer weights, BF16 serve", _m(34), None,
              live=None, infra="setup"),
        Block("t0-probe", "Probe / go-no-go: power-cap write + image acceptance on the server",
              _m(8), _py("wattnot.vision.imageprobe", "--fake"),
              live=["bash", "scripts/glimmer_probe.sh", HOST_BASE_URL], host=True),
        Block("t1-sweep", "Glimmer BF16 power-cap sweep + signed receipt (quick, 2 control blocks)",
              _m(22), sweep_dry, live=sweep_live),
        Block("t1-resume", "Forced-SSH-drop resume test: a short BF16 workload the driver re-runs "
              "after the link drops mid-block", _m(5), tiers_dry("t1-resume", "muse-glimmer-30b"),
              live=tiers_live("t1-resume", "muse-glimmer-30b"), host=True),
        Block("t1-eye", "BF16 eye test: served vision reads the images (90 s budget); a miss "
              "rejects FP8 below", _m(3), eye_dry("t1-eye"), live=eye_live("t1-eye"), host=True),
        Block("t1-spoken", "BF16 spoken answers: Glimmer answers the recorded questions (clean + "
              "data-center noise, 90 s budget); J/correct spoken reported", _m(3),
              spoken_dry("t1-spoken"), live=spoken_live("t1-spoken"), host=True),
        Block("t2-fp8", "FP8 swap: restart Glimmer FP8, memory + start time + the same mini tiers",
              _m(20), tiers_dry("t2-fp8", "muse-glimmer-30b-fp8"),
              live=tiers_live("t2-fp8", "muse-glimmer-30b-fp8"), host=True),
        Block("t2-eye", "FP8 eye test: the quant candidate must read the same images or roll back",
              _m(3), eye_dry("t2-eye"), live=eye_live("t2-eye"), host=True,
              depends_on=("t2-fp8",)),
        Block("t2-spoken", "FP8 spoken answers: the quant candidate must answer the same questions "
              "or roll back", _m(3), spoken_dry("t2-spoken"), live=spoken_live("t2-spoken"),
              host=True, depends_on=("t2-fp8",)),
        # OPTIONAL batch-size / KV-cache sweep: off in the 2 h plan (needs an explicit opt-in and
        # is dropped if the time left cannot cover it). Not counted in the 100-110 min budget.
        Block("t2b-batch", "OPTIONAL batch-size / KV-cache sweep: restart Glimmer per "
              "(max-num-seqs, kv-dtype) combo and measure", _m(15),
              _py("wattnot.longrun.harness", "--cap", "350", "--out", str(out / "runs" / "t2b-batch"),
                  "--fake", "--model-config", "muse-glimmer-30b", "--per-tier", "2"),
              live=["bash", "scripts/batch_kv_sweep.sh", "bf16"], host=True, optional=True),
        Block("t3-copy", "Copy results back to the control box", _m(5), None, live=None,
              infra="pull"),
    ]


class VmControl(Protocol):
    def alive(self) -> bool: ...
    def api_state(self) -> str | None: ...
    def pull(self) -> bool: ...
    def delete(self) -> None: ...


@dataclass
class FakeVm:
    """A scripted VM. `drop` = {block_id: n} makes SSH fail n probes after that block runs."""
    drop: dict = field(default_factory=dict)
    dead_after: str | None = None
    down: int = 0
    deleted: bool = False
    pulls: int = 0
    log: list = field(default_factory=list)

    def arm(self, block_id: str) -> None:
        self.down = self.drop.pop(block_id, 0)  # each scripted drop happens once
        if self.dead_after == block_id:
            self.down = 10 ** 6

    def alive(self) -> bool:
        if self.down > 0:
            self.down -= 1
            return False
        return not self.deleted

    def api_state(self) -> str | None:
        return "deleted" if self.deleted else "running"

    def pull(self) -> bool:
        self.pulls += 1
        return self.alive()

    def delete(self) -> None:
        self.deleted = True
        self.log.append("delete")


@dataclass
class RemoteVm:
    """A live VM already launched by scripts/e2e/ctl.py. All teardown goes through ctl.py so the
    one-VM lock and the DELETE+verify stay in one place; nothing here ever launches a VM."""
    host: str
    key: str = "~/.ssh/e2e_h100"
    out_dir: Path = Path("run3-pulled")
    _deleted: bool = False

    def alive(self) -> bool:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("run2_remote", RUN2_REMOTE)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return bool(mod.is_alive(self.host, self.key))
        except Exception:  # noqa: BLE001 - any failure to reach the host counts as not alive
            return False

    def api_state(self) -> str | None:
        return "deleted" if self._deleted else "running"

    def pull(self) -> bool:
        rc = subprocess.run([sys.executable, str(RUN2_REMOTE), "pull", "--host", self.host,
                             "--key", self.key, "--out", str(self.out_dir)]).returncode
        return rc == 0

    def delete(self) -> None:
        subprocess.run([sys.executable, str(CTL), "delete"])
        self._deleted = True


class Driver:
    def __init__(self, out: Path, blocks: list[Block], vm: VmControl, *, label: str,
                 run: Callable[..., int], max_hours: float, balance_inr: Callable[[], float],
                 hours_used: Callable[[], float], capture: bool = False, include_optional=(),
                 sleep=time.sleep, clock=time.monotonic, log=print,
                 heartbeat_path: Path | None = None, wallclock: Callable[[], float] = time.time,
                 balance_floor_inr: float = BALANCE_FLOOR_INR, publisher=None,
                 watchdog_margin_h: float = 0.0) -> None:
        self.out, self.blocks, self.vm, self.label = out, blocks, vm, label
        self.run, self.max_hours, self.balance, self.used = run, max_hours, balance_inr, hours_used
        self.capture, self.include = capture, set(include_optional)
        self.sleep, self.clock, self.log = sleep, clock, log
        # Heartbeat the three watchdog layers read (wattnot.longrun.watchdog). Opt-in: off when
        # no path is given, so the watchdogs never see a stale beat from an unrelated run.
        self.heartbeat_path, self.wallclock = heartbeat_path, wallclock
        # Optional off-box publisher (wattnot.longrun.watchdog.publisher). When set, each local
        # heartbeat is also pushed off-box for layers 4/5 to read. Dry-run by default, rate-limited
        # and failure-tolerant on its own, so it can never crash or slow the run.
        self.publisher = publisher
        self.balance_floor = balance_floor_inr
        # Stop this many hours before the hard cap, matching the off-box watchdog, so the driver
        # tears down cleanly instead of being killed mid-block (ctl.py deletes at deadline - 8 min).
        self.watchdog_margin_h = watchdog_margin_h
        self._run_arity = self._arity(run)
        self._launch_wall = wallclock()
        self.state_path = out / "driver_state.json"
        self.state = (json.loads(self.state_path.read_text()) if self.state_path.exists()
                      else {"blocks": {}, "events": [], "stopped": None})
        self.rec = Recorder(out, out / "live.jsonl", label=label)

    @staticmethod
    def _arity(run) -> int:
        try:
            return len(inspect.signature(run).parameters)
        except (ValueError, TypeError):
            return 1

    def _deadline_h(self) -> float:
        """The usable hours: the hard cap minus the watchdog margin."""
        return self.max_hours - self.watchdog_margin_h

    def _invoke(self, b: Block, timeout_s: float) -> int:
        """Run a block, bounded by `timeout_s`. Runners that take a second arg get the budget so
        they can kill an overrunning command; one-arg runners (tests) are called unchanged."""
        return self.run(b, timeout_s) if self._run_arity >= 2 else self.run(b)

    def _save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, default=str))
        os.replace(tmp, self.state_path)

    def _note(self, **kw) -> None:
        kw["h"] = round(self.used(), 2)
        self.state["events"].append(kw)
        self.log(json.dumps(kw, default=str))
        self._save()

    def _heartbeat(self, block: str | None = None, note: str | None = None) -> None:
        """Refresh the beat the watchdog layers poll. No-op unless a path was given."""
        if self.heartbeat_path is None:
            return
        from wattnot.longrun.watchdog.core import Heartbeat, write_heartbeat
        now = self.wallclock()
        hb = Heartbeat(
            ts=now, launch_ts=self._launch_wall,
            deadline_ts=self._launch_wall + self.max_hours * 3600.0,
            spent_inr=round(self.used() * INR_PER_H, 2), block=block, note=note)
        write_heartbeat(self.heartbeat_path, hb)
        if self.publisher is not None:
            try:  # the publisher is already failure-tolerant; this guard is belt-and-braces
                self.publisher.publish(hb.as_dict())
            except Exception:  # noqa: BLE001 - publishing must never interrupt the run
                pass

    def _teardown(self, **kw):
        d = plan_teardown(ssh_alive=self.vm.alive, api_state=self.vm.api_state, pull=self.vm.pull,
                          sleep=self.sleep, clock=self.clock, **kw)
        self._note(kind="teardown-decision", **{k: v for k, v in d.as_dict().items()
                                                if k != "probes"}, probes=len(d.probes))
        if d.delete:
            self.vm.delete()
            self._note(kind="vm-deleted", reason=d.reason)
        return d

    def _receipt(self, d: Path, block_id: str) -> None:
        """A hash-chained receipt per block: what was saved, and the previous block's hash, so a
        lost or edited block shows. The signed run receipts come from orchestrate itself."""
        import hashlib
        files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(d.iterdir()) if p.is_file() and p.name != "receipt.json"}
        prev = self.state.get("last_receipt")
        body = {"format": "wattnot.block-receipt/1", "block": block_id, "label": self.label,
                "files": files, "prev": prev}
        h = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        (d / "receipt.json").write_text(json.dumps({**body, "sha256": h}, indent=1))
        self.state["last_receipt"] = h

    def _block_timeout_s(self, b: Block, left_h: float) -> float:
        """Hard wall-clock for a block: the smaller of its own budget (with slack) and the time
        left to the deadline. A command that blows past this is killed (task: in-block timeout)."""
        budget_s = b.hours * 3600.0 * OVERRUN_FACTOR
        deadline_s = max(1.0, left_h * 3600.0)
        return max(1.0, min(budget_s, deadline_s))

    def go(self) -> dict:
        self._heartbeat(note="start")
        for b in self.blocks:
            self._heartbeat(block=b.id)
            st = self.state["blocks"].get(b.id, {})
            if st.get("status") in ("done", "skipped", "dropped"):
                continue
            if b.optional and b.id not in self.include:
                self.state["blocks"][b.id] = {"status": "skipped", "why": "needs Akash's yes"}
                self._save()
                continue
            # a block whose dependency did not finish 'done' is skipped, not run. This is why a
            # dropped t2-fp8 skips its FP8 eye/spoken gates instead of gating a candidate that
            # never existed.
            bad = [d for d in b.depends_on
                   if self.state["blocks"].get(d, {}).get("status") != "done"]
            if bad:
                self.state["blocks"][b.id] = {"status": "skipped",
                                              "why": f"dependency not done: {','.join(bad)}"}
                self._save()
                continue
            bal = self.balance()
            # time left = the smaller of the usable deadline and what the credit above the floor buys
            left = min(self._deadline_h() - self.used(), (bal - self.balance_floor) / INR_PER_H)
            if bal < self.balance_floor or self.used() >= self._deadline_h():
                self._teardown(why=f"before {b.id}", balance_low=bal < self.balance_floor,
                               hard_deadline_hit=self.used() >= self._deadline_h())
                self.state["stopped"] = "money rule"
                break
            if b.hours > left and b.min_hours is not None and b.min_hours <= left:
                self._note(kind="trimmed", block=b.id, hours=b.min_hours, left=round(left, 2))
                b = replace(b, title=b.title + " (trimmed)", hours=b.min_hours, dry=b.trim,
                            live=b.trim_live if b.trim_live is not None else b.live)
            if b.hours > left:
                self.state["blocks"][b.id] = {"status": "dropped",
                                              "why": f"needs {b.hours} h, {left:.2f} h left"}
                self._save()
                continue
            for attempt in (1, 2):
                info = self.rec.begin(b.id, capture=self.capture and b.capture)
                t0 = time.time()
                if hasattr(self.vm, "arm"):
                    self.vm.arm(b.id)
                # recompute the time left so a long first attempt shrinks the retry's budget
                left_now = min(self._deadline_h() - self.used(),
                               (self.balance() - self.balance_floor) / INR_PER_H)
                timeout_s = self._block_timeout_s(b, left_now)
                rc = self._invoke(b, timeout_s)
                if rc == 124:
                    self._note(kind="overran", block=b.id, timeout_s=round(timeout_s, 1),
                               attempt=attempt)
                linkdown = rc in LINK_LOST_RCS or not self.vm.alive()
                result = {"rc": rc, "attempt": attempt, "wall_s": round(time.time() - t0, 1),
                          **info}
                if linkdown:
                    d = self._teardown(why=f"{b.id} link lost (rc {rc})")
                    result["teardown"] = d.reason
                    if d.delete:
                        self.rec.end(result)
                        self.state["blocks"][b.id] = {"status": "lost", **result}
                        self.state["stopped"] = "VM dead"
                        self._save()
                        return self.state
                copied = self.vm.pull()
                result["copied"] = copied
                tiers = self.out / "runs" / b.id.split("-")[0] / "tiers_summary.json"
                if tiers.exists():   # the tier table goes into the block's recorded state
                    result["tiers"] = json.loads(tiers.read_text())
                d = self.rec.end(result)
                self._receipt(d, b.id)
                ok = rc == 0 and not linkdown
                self.state["blocks"][b.id] = {"status": "done" if ok else "failed", **result}
                self._save()
                if ok:
                    break
                self._note(kind="retry" if attempt == 1 else "gave-up", block=b.id, rc=rc)
                # the retry runs the trimmed version when there is one, so one lost link does
                # not push the blocks after it off the plan (g2 full redo = 2.75 h, trimmed 1.5 h)
                if attempt == 1 and b.min_hours is not None and b.trim is not None \
                        and not b.title.endswith("(trimmed)"):
                    self._note(kind="trimmed", block=b.id, hours=b.min_hours, left=None, why="retry")
                    b = replace(b, title=b.title + " (trimmed)", hours=b.min_hours, dry=b.trim,
                                live=b.trim_live if b.trim_live is not None else b.live)
        if not self.state["stopped"] and not getattr(self.vm, "deleted", False):
            d = self._teardown(why="end of plan", finished=True)
            self.state["stopped"] = "finished" if d.delete else "finished, VM kept (copy failed)"
        self._save()
        return self.state


def dry_run(out: Path, *, drop: dict | None = None, dead_after: str | None = None,
            include_optional=(), runner=None, timeout_s: float = 900, model: str = "llama",
            max_hours: float = 18.0) -> dict:  # the plan's watchdog hard cap
    out.mkdir(parents=True, exist_ok=True)
    blocks = plan(out, model)
    sim = {"h": 0.0}
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    env.pop("WATTNOT_META_KEY", None)

    def run(b: Block, budget_s: float = timeout_s) -> int:
        sim["h"] += b.hours
        if runner is not None:
            return runner(b)
        if b.dry is None:
            return 0
        logf = out / "logs" / f"{b.id}.log"
        logf.parent.mkdir(parents=True, exist_ok=True)
        with logf.open("w") as f:
            try:
                return subprocess.run(b.dry, stdout=f, stderr=subprocess.STDOUT, env=env,
                                      timeout=min(timeout_s, budget_s)).returncode
            except subprocess.TimeoutExpired:
                return 124

    vm = FakeVm(drop=drop or {}, dead_after=dead_after)
    t = {"now": 0.0}

    def sleep(s):
        t["now"] += s

    margin = WATCHDOG_MARGIN_H if model == "glimmer-test" else 0.0
    d = Driver(out, blocks, vm, label="MOCK", run=run, max_hours=max_hours,
               balance_inr=lambda: 9501.60 - sim["h"] * INR_PER_H, hours_used=lambda: sim["h"],
               sleep=sleep, clock=lambda: t["now"], include_optional=include_optional,
               heartbeat_path=out / "heartbeat.json", watchdog_margin_h=margin)
    state = d.go()
    state["sim"] = {"hours": sim["h"], "inr": round(sim["h"] * INR_PER_H, 0),
                    "usd": round(sim["h"] * INR_PER_H / INR_PER_USD, 2),
                    "vm_deleted": vm.deleted, "pulls": vm.pulls}
    (out / "driver_state.json").write_text(json.dumps(state, indent=1, default=str))
    return state


def _live_runner(out: Path, host: str, key: str, vm: RemoteVm, log=print):
    """Build the live `run(b, timeout_s)` callable. Host blocks run the REAL command over SSH on
    the GPU host (under the remote venv); control blocks run locally (orchestrate SSHes itself).
    No block ever uses --fake: the live argv is the real command."""
    env = dict(os.environ)

    def fill(argv: list[str]) -> list[str]:
        return [a.replace("{HOST}", host).replace("{KEY}", key) for a in argv]

    def run(b: Block, budget_s: float) -> int:
        if b.infra == "setup":
            return subprocess.run([sys.executable, str(RUN2_REMOTE), "setup", "--host", host,
                                   "--key", key, "--model", GLIMMER_MODEL, "--moe", "none"],
                                  timeout=budget_s).returncode
        if b.infra == "pull":
            return 0 if vm.pull() else 1
        if b.live is None:
            return 0
        argv = fill(b.live)
        logf = out / "logs" / f"{b.id}.log"
        logf.parent.mkdir(parents=True, exist_ok=True)
        if b.host:
            from wattnot.proof import sshutil
            inner = (f"cd {REMOTE_REPO} && . venv/bin/activate 2>/dev/null; "
                     + " ".join(shlex.quote(a) for a in argv))
            try:
                c = sshutil.connect(host, key)
            except Exception as e:  # noqa: BLE001
                log(f"[live] SSH connect failed for {b.id}: {type(e).__name__}")
                return 5
            try:
                rc, raw = sshutil.exec_bounded(c.get_transport(), inner, timeout=budget_s)
            except TimeoutError:
                return 124
            except sshutil.SshDown:
                return 4
            except Exception as e:  # noqa: BLE001 - a dead link mid-command
                log(f"[live] SSH exec lost for {b.id}: {type(e).__name__}")
                return 4
            finally:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass
            logf.write_bytes(raw)
            return rc
        with logf.open("w") as f:
            try:
                return subprocess.run(argv, stdout=f, stderr=subprocess.STDOUT, env=env,
                                      timeout=budget_s).returncode
            except subprocess.TimeoutExpired:
                return 124

    return run


def live_run(out: Path, *, host: str, key: str = "~/.ssh/e2e_h100", model: str = "glimmer-test",
             max_hours: float | None = None, include_optional=()) -> dict:
    """Drive a REAL VM (already launched by scripts/e2e/ctl.py). Runs the no-mock preflight first
    and refuses to start if any live input or step is a mock/placeholder/stub/replay. Never
    launches a VM; teardown goes through ctl.py."""
    out.mkdir(parents=True, exist_ok=True)
    from wattnot.longrun import nomock
    report = nomock.audit_plan(model)
    nomock.write_report(report, out / "nomock-preflight.json")
    if not report["ok"]:
        print("NO-MOCK preflight FAILED; refusing to start the live run:", file=sys.stderr)
        for f in report["findings"]:
            print(f"  - {f['where']}: {f['what']}", file=sys.stderr)
        return {"stopped": "no-mock preflight failed", "nomock": report, "blocks": {}}

    blocks = plan(out, model)
    vm = RemoteVm(host=host, key=key, out_dir=out / "pulled")
    hours = max_hours if max_hours is not None else (2.5 if model == "glimmer-test" else 18.0)
    run = _live_runner(out, host, key, vm)

    def balance_inr() -> float:
        try:
            return _ctl_balance()
        except Exception:  # noqa: BLE001 - if the balance API is unreachable, assume healthy
            return 10_000.0

    d = Driver(out, blocks, vm, label="MEASURED", run=run, max_hours=hours,
               balance_inr=balance_inr, hours_used=lambda: (time.time() - d._launch_wall) / 3600.0,
               include_optional=include_optional, heartbeat_path=out / "heartbeat.json",
               watchdog_margin_h=WATCHDOG_MARGIN_H if model == "glimmer-test" else 0.0)
    return d.go()


def _ctl_balance() -> float:
    import importlib.util
    spec = importlib.util.spec_from_file_location("ctl", CTL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return float(mod.balance())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true",
                   help="CPU + mocks, no VM; every number is labelled MOCK/SIMULATED")
    g.add_argument("--live", action="store_true",
                   help="real VM (launch it first with scripts/e2e/ctl.py) + real commands; "
                        "runs the no-mock preflight and refuses any mock input")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", choices=["glimmer", "llama", "glimmer-test"], default="glimmer",
                    help="glimmer = primary plan; llama = fallback; glimmer-test = the 2 h test VM")
    ap.add_argument("--host", help="live: the GPU host's IP (default: read scripts/e2e ip file)")
    ap.add_argument("--key", default="~/.ssh/e2e_h100", help="live: SSH key for the GPU host")
    ap.add_argument("--max-hours", type=float, default=None)
    ap.add_argument("--drop-link", default="b2-wonder-sweep:3",
                    help="dry: simulate an SSH drop: block:probes ('' for none)")
    a = ap.parse_args(argv)

    if a.live:
        host = a.host
        if not host:
            ip_file = CTL.parent  # scripts/e2e/ -- the ctl D dir holds the ip file on the box
            cand = Path("/workspace/hackathon/e2e-glimmer-test/ip")
            host = cand.read_text().strip() if cand.exists() else None
        if not host:
            print("--live needs --host IP (or an ip file written by scripts/e2e/ctl.py status)",
                  file=sys.stderr)
            return 2
        st = live_run(a.out, host=host, key=a.key, model=a.model, max_hours=a.max_hours)
        for k, v in st.get("blocks", {}).items():
            print(f"{k:18} {v.get('status', '?'):8} rc={v.get('rc', '-')} "
                  f"{v.get('wall_s', '')}s {v.get('why', v.get('teardown', ''))}")
        print("stopped:", st.get("stopped"))
        return 0 if all(v.get("status") in ("done", "skipped", "dropped")
                        for v in st.get("blocks", {}).values()) else 1

    drop = {}
    if a.drop_link:
        k, n = a.drop_link.split(":")
        drop[k] = int(n)
    if a.model != "llama" and a.drop_link == "b2-wonder-sweep:3":
        drop = {"g2-wonder-sweep": 3} if a.model == "glimmer" else {"t1-resume": 2}
    hours = a.max_hours or (2.5 if a.model == "glimmer-test" else 18.0)
    st = dry_run(a.out, drop=drop, model=a.model, max_hours=hours)
    for k, v in st["blocks"].items():
        print(f"{k:18} {v['status']:8} rc={v.get('rc', '-')} {v.get('wall_s', '')}s "
              f"{v.get('why', v.get('teardown', ''))}")
    print("stopped:", st["stopped"], "| sim:", st["sim"])
    return 0 if all(v["status"] in ("done", "skipped", "dropped")
                    for v in st["blocks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
