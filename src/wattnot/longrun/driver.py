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
import contextlib
import inspect
import json
import os
import shlex
import signal
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
PULL_TIMEOUT_S = 600.0     # a copy-back must not hang forever (RUN3: a pull wedged 14+ min)


# Process groups of control-machine children (run2_remote setup / pull) the driver started. Each
# child runs in its OWN session, so the driver can kill the WHOLE group on timeout or on stop and
# never leave an orphaned setup dialing a deleted VM's IP (RUN3, attempt 2).
_CHILD_PGIDS: set[int] = set()


def kill_child_groups(sig: int = signal.SIGTERM) -> None:
    """Signal every child process group the driver started, then forget them."""
    for pgid in list(_CHILD_PGIDS):
        with contextlib.suppress(OSError):
            os.killpg(pgid, sig)
        _CHILD_PGIDS.discard(pgid)


def run_child(argv: list[str], *, timeout: float, env=None) -> int:
    """Run a control-machine child in its own process group so the whole group can be killed on
    timeout or on a driver stop. Returns the exit code, or 124 if it overran `timeout`."""
    p = subprocess.Popen(argv, env=env, start_new_session=True)
    try:
        pgid = os.getpgid(p.pid)
    except OSError:
        pgid = None
    if pgid is not None:
        _CHILD_PGIDS.add(pgid)
    try:
        return p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if pgid is not None:
            with contextlib.suppress(OSError):
                os.killpg(pgid, signal.SIGKILL)
        else:
            p.kill()
        with contextlib.suppress(Exception):
            p.wait(timeout=10)
        return 124
    finally:
        if pgid is not None:
            _CHILD_PGIDS.discard(pgid)

# Remote layout a live run drives (matches scripts/run2_remote.py and h100_vm_setup.sh).
REMOTE_REPO = "/root/wattnot"
# h100_vm_setup.sh builds the venv at $ROOT/venv (/root/venv), NOT inside the repo. A host block
# must activate THAT path; the old `cd /root/wattnot && . venv/bin/activate` looked for
# /root/wattnot/venv, which does not exist, so `python` was missing and every host block failed
# rc 127 (RUN3, Oct 31). Keep this in step with h100_vm_setup.sh's VENV=$ROOT/venv.
REMOTE_VENV = "/root/venv"
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
    skip_if_short: bool = False    # time left can't cover it -> log "skipped: time" and move on


def _py(*args: str) -> list[str]:
    """A control-machine command (this interpreter)."""
    return [sys.executable, "-m", *args]


def _rpy(*args: str) -> list[str]:
    """A GPU-host command, run under the remote venv (`python` after activate)."""
    return ["python", "-m", *args]


def best_cap_from_sweep(out: Path) -> int | None:
    """The best accepted cap from the sweep's results.json (written on the control box by
    orchestrate), for the combined block's prefix-ON arm. None when the sweep produced no passing
    cap — the combined block then skips cleanly instead of measuring against nothing."""
    p = out / "runs" / "t1-sweep" / "vm" / "results.json"
    try:
        bc = json.loads(p.read_text()).get("best_cap_w")
    except (OSError, ValueError):
        return None
    try:
        return int(bc) if bc else None
    except (TypeError, ValueError):
        return None


def host_inner(argv: list[str]) -> str:
    """The shell line for a GPU-host block: cd into the repo, activate the venv that
    h100_vm_setup.sh built at REMOTE_VENV, then run the command. If the venv is missing the block
    stops loudly (rc 97 with a clear message on stderr) instead of running a `python` that is not
    on PATH and failing rc 127 with no hint (the RUN3 venv-path mismatch). stderr is NOT swallowed,
    so a bad activate shows up in the block log."""
    cmd = " ".join(shlex.quote(a) for a in argv)
    activate = f"{REMOTE_VENV}/bin/activate"
    return (f"cd {shlex.quote(REMOTE_REPO)} || exit 96; "
            f"if [ ! -f {shlex.quote(activate)} ]; then "
            f"echo '[live] venv missing: {activate} (setup builds it)' >&2; exit 97; fi; "
            f". {shlex.quote(activate)} || {{ echo '[live] cannot activate {activate}' >&2; "
            f"exit 97; }}; " + cmd)


def _m(minutes: float) -> float:
    return minutes / 60.0


def plan(out: Path, model: str = "llama", plan_name: str = "full") -> list[Block]:
    """The block list. model="llama" is plan A (Llama 8B main, Glimmer optional);
    model="glimmer" is plan B (Glimmer 30B main; Llama RUN2 stays as prior evidence).
    plan_name="retest" (glimmer-test only) re-runs just setup, probe, the sweep and the RUN3
    failures, sized to a 2 h VM."""
    if model == "glimmer":
        return plan_glimmer(out)
    if model == "glimmer-test":
        return plan_glimmer_retest(out) if plan_name == "retest" else plan_glimmer_test(out)
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


def sweep_live_argv(out: Path, live_file: Path, guard_cfg: str, *, sweep_budget_s: int,
                    reps: int, warmup: int, warmup_s: int, grid: str) -> list[str]:
    """The live BF16 power-cap sweep command. The planner is the REAL Wonder (``--wonder-live``),
    never the deterministic "muse-wonder-mock" (RUN3 shipped the mock because this flag was missing;
    nomock.audit_plan now fails a live wonder sweep without it). The budget/warm-up/rounds are tuned
    by the caller so the four grid caps are measured inside the block: RUN3 fit only one cap because
    a 16-request warm-up took ~169 s per arm on a reasoning model. The numbers stay MEASURED, just
    with a shorter warm-up; the 375/350/325/300 W grid fills every cap even if the planner stops
    early. 300 W is the floor: it is the deepest Wonder candidate and stays above the ~294 W floor
    (58% max drop from 700 W stock; orchestrate refuses live caps below it). The approval demo
    policy allows a 58% drop, so it approves every sweep cap that passes the gates."""
    return _py("wattnot.proof.orchestrate", "--mode", "live", "--host", "{HOST}",
               "--key", "{KEY}", "--model", GLIMMER_MODEL, "--out", str(out / "runs" / "t1-sweep"),
               "--profile", "quick", "--planner", "wonder", "--wonder-live", "--no-adaptive",
               "--fresh", "--sweep-budget-s", str(sweep_budget_s), "--warmup", str(warmup),
               "--reps", str(reps), "--grid", grid,
               # Glimmer reasons before answering: floor each request so proof-v1's small QA/math
               # caps are not cut off (the gap test lost 64/64 QA answers). Thinking stays ON.
               "--reasoning-floor", "1024",
               "--sign-key", "receipt-offline-key.pem",
               "--sam", "live", "--warmup-s", str(warmup_s), "--burn-in", "1", "--counterbalance",
               "--approval-policy", APPROVAL_POLICY, "--policy-hold-s", "20",
               # muse-wonder must be a standard tier, or a 300-375 W cap (high risk) goes
               # PENDING_STRICT, the operator policy is never consulted, and the sweep waits
               # 600 s then rolls the measured cap back as "no approval"
               "--guardrail-config", guard_cfg,
               "--live-file", str(live_file))


def plan_glimmer_test(out: Path) -> list[Block]:
    """The 3.5 h Glimmer test VM, GPU-first so the core proof is saved before anything optional.

    Order: setup -> probe (cap write + image acceptance) -> BF16 power-cap sweep + signed receipt
    -> a short BF16 block with resume armed (the forced drop is dry-run only) -> eye + spoken on BF16 -> FP8 swap -> eye + spoken on FP8
    -> copy results back. Two prompts per tier. Budgeted to 162 min of the 202 usable (3.5 h
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

    guard_cfg = str(Path(__file__).resolve().parents[3] / "examples/longrun/guardrails-wonder.json")
    # the dry sweep takes the live approval path (operator policy + hold + Wonder guardrail tiers,
    # warm-up, burn-in, counterbalance, signing), not --auto-approve: round 6 found two live-only
    # bugs that --auto-approve hid. MOCK numbers can never pass a policy, so the dry run shows the
    # decline -> reject -> roll back path; the live run differs only in mode, host, model, SAM.
    sweep_dry = _py("wattnot.proof.orchestrate", "--mode", "rehearsal", "--out",
                    str(out / "runs" / "t1-sweep"), "--profile", "quick", "--planner", "wonder",
                    "--no-adaptive", "--fresh", "--sign-key", "receipt-offline-key.pem",
                    "--warmup-s", "10", "--burn-in", "1", "--counterbalance",
                    "--approval-policy", str(out / DRY_POLICY_NAME), "--policy-hold-s", "2",
                    "--guardrail-config", guard_cfg, "--live-file", str(live_file))
    sweep_live = sweep_live_argv(out, live_file, guard_cfg, sweep_budget_s=1500, reps=2,
                                 warmup=4, warmup_s=5, grid="375,350,325,300")
    sweep_dir = out / "runs" / "t1-sweep"
    py = shlex.quote(sys.executable)
    wonder_live = ["bash", "-c",
                   f"{py} scripts/sweep_to_trials.py {shlex.quote(str(sweep_dir))} && "
                   f"{py} -m wattnot.wonderchart.run --live --sweep "
                   f"{shlex.quote(str(sweep_dir / 'sweep_trials.json'))} "
                   f"--out {shlex.quote(str(sweep_dir / 'wonder'))}"]
    # dry: the same sweep_to_trials step on the dry sweep, then the offline chart (no paid API)
    wonder_dry = ["bash", "-c", wonder_live[2].replace(" --live ", " --fake ")]
    eu_out = sweep_dir / "eu" / "eu-2024-1364"
    # proof-*.json also matches the proof-<id>.sigstore.json bundle, which is not a receipt
    # (export would KeyError and t1-eu would fail every run): pass the receipts only.
    eu_cmd = (f"set -o pipefail; rcpts=$(ls {shlex.quote(str(sweep_dir))}/proof-*.json "
              f"| grep -v '\\.sigstore\\.json$') && "
              f"{py} -m wattnot.cli report eu --receipts $rcpts "
              f"--out {shlex.quote(str(eu_out))}")
    fp8_live = ["bash", "-c", "bash scripts/glimmer_serve.sh fp8 && "
                + " ".join(shlex.quote(a) for a in tiers_live("t2-fp8", "muse-glimmer-30b-fp8"))]

    return [
        Block("t0-setup", "Launch, setup, Glimmer weights, BF16 serve", _m(34), None,
              live=None, infra="setup"),
        Block("t0-probe", "Probe / go-no-go: power-cap write + image acceptance on the server",
              _m(8), _py("wattnot.vision.imageprobe", "--fake"),
              live=["bash", "scripts/glimmer_probe.sh", HOST_BASE_URL], host=True),
        Block("t1-sweep", "Glimmer BF16 power-cap sweep + live SAM 3.1 rack scan + signed receipt "
              "(quick, 2 control blocks)", _m(35), sweep_dry, live=sweep_live,
              depends_on=("t0-probe",)),   # no cap write / no images = no sweep
        Block("t1-wonder", "Muse Wonder reads the measured sweep chart and names the next cap "
              "(control box, paid API, cents)", _m(2),
              wonder_dry, live=wonder_live, depends_on=("t1-sweep",)),
        Block("t1-eu", "EU 2024/1364 field export from the signed sweep receipt (control box, "
              "no GPU time; checks the receipt's SHA-256 first)", _m(1),
              ["bash", "-c", eu_cmd],
              live=["bash", "-c", eu_cmd], depends_on=("t1-sweep",)),
        Block("t1-resume", "Short BF16 workload with the resume path armed: the driver re-runs it "
              "if the link drops. The forced drop is injected in the dry run only (MOCK); live, "
              "resume runs only on a real drop, so a live pass is not a resume proof", _m(5), tiers_dry("t1-resume", "muse-glimmer-30b"),
              live=tiers_live("t1-resume", "muse-glimmer-30b"), host=True),
        Block("t1-eye", "BF16 eye test: served vision reads the images (90 s budget); a miss "
              "rejects FP8 below", _m(3), eye_dry("t1-eye"), live=eye_live("t1-eye"), host=True),
        Block("t1-spoken", "BF16 spoken answers: Glimmer answers the recorded questions (clean + "
              "data-center noise, 90 s budget); J/correct spoken reported", _m(3),
              spoken_dry("t1-spoken"), live=spoken_live("t1-spoken"), host=True),
        Block("t2-fp8", "FP8 swap: restart Glimmer FP8, memory + start time + the same mini tiers",
              _m(20), tiers_dry("t2-fp8", "muse-glimmer-30b-fp8"), live=fp8_live, host=True),
        Block("t2-eye", "FP8 eye test: the quant candidate must read the same images or roll back",
              _m(3), eye_dry("t2-eye"), live=eye_live("t2-eye"), host=True,
              depends_on=("t2-fp8",)),
        Block("t2-spoken", "FP8 spoken answers: the quant candidate must answer the same questions "
              "or roll back", _m(3), spoken_dry("t2-spoken"), live=spoken_live("t2-spoken"),
              host=True, depends_on=("t2-fp8",)),
        # Extra-hour blocks (3.5 h booking): after every core block, so the driver drops them
        # first if Glimmer runs slow. Each restarts the server itself.
        Block("t2b-batch", "Batch-size / KV-cache sweep: restart Glimmer per "
              "(max-num-seqs, kv-dtype) combo and measure (2 combos, ~450 s each)", _m(20),
              _py("wattnot.longrun.harness", "--cap", "350", "--out", str(out / "runs" / "t2b-batch"),
                  "--fake", "--model-config", "muse-glimmer-30b", "--per-tier", "2"),
              live=["bash", "scripts/batch_kv_sweep.sh", "bf16"], host=True),
        Block("t2c-prefix", "Prefix cache off vs on, interleaved, 3 reps (J/correct, hit rate, "
              "TTFT, p99, quality gate)", _m(25),
              [sys.executable, "scripts/prefix_cache_ab.py", "--fake", "--out",
               str(out / "runs" / "t2c-prefix")],
              live=["python", "scripts/prefix_cache_ab.py", "--out", f"{REMOTE_RESULTS}/t2c-prefix",
                    "--reps", "3"], host=True),
        # The headline WattNot-vs-stock number (best cap + prefix ON vs stock 700 W + prefix OFF).
        # The retest had it; the long run must too, with the full 3 reps.
        _combined_block(out, minutes=15, reps=3),
        Block("t3-copy", "Copy results back to the control box", _m(5), None, live=None,
              infra="pull"),
    ]


# The blocks a retest re-runs: setup, probe, the sweep, and the blocks that failed in RUN3
# (t1-resume, t1-eye, t1-spoken, t2-fp8 and its FP8 eye/spoken gates, t2b-batch, t2c-prefix), then
# the combined block (item 12) and the copy. t1-wonder / t1-eu passed and are dropped. The combined
# block is built here (not in plan_glimmer_test) and inserted before t3-copy.
RETEST_BLOCKS = ("t0-setup", "t0-probe", "t1-sweep", "t1-resume", "t1-eye", "t1-spoken",
                 "t2-fp8", "t2-eye", "t2-spoken", "t2b-batch", "t2c-prefix", "t3-copy")
# Trimmed per-block budgets (minutes) so the retest, WITH the combined block, fits a 2 h VM (112 min
# usable after the 8 min watchdog margin). Setup (weight load) and the gates are fixed; the sweep,
# FP8 and the two extras are trimmed to make room, but every previously failed block is kept.
# Unlisted blocks keep their plan_glimmer_test budget. Sum (incl. t2d-combined) = 111 min.
RETEST_HOURS_MIN = {"t0-probe": 5, "t1-sweep": 16, "t1-resume": 3, "t2-fp8": 13,
                    "t2b-batch": 8, "t2c-prefix": 7, "t2d-combined": 8}


def _combined_block(out: Path, minutes: float | None = None, reps: int = 2) -> Block:
    """Item 12: best passing cap + prefix caching ON vs the stock baseline (700 W, prefix OFF),
    interleaved reps. {BEST_CAP} is filled from the sweep's results.json at run time; if the sweep
    found no passing cap the script prints a skip and exits 0. skip_if_short=True so a slow upstream
    block drops THIS block (logged 'skipped: time'), never a previously failed one."""
    return Block(
        "t2d-combined",
        "Combined: best passing cap + vLLM prefix caching ON vs stock 700 W + prefix OFF, "
        "interleaved (MEASURED J/request, J/correct, quality + p99 gates, combined saving %)",
        _m(RETEST_HOURS_MIN["t2d-combined"] if minutes is None else minutes),
        [sys.executable, "scripts/prefix_cache_ab.py", "--fake", "--combined",
         "--cap-on", "325", "--cap-off", "700", "--reps", str(reps),
         "--out", str(out / "runs" / "t2d-combined")],
        live=["python", "scripts/prefix_cache_ab.py", "--combined", "--cap-on", "{BEST_CAP}",
              "--cap-off", str(int(H100_STOCK_W)), "--reps", str(reps),
              "--out", f"{REMOTE_RESULTS}/t2d-combined"],
        host=True, depends_on=("t1-sweep",), skip_if_short=True)


def plan_glimmer_retest(out: Path) -> list[Block]:
    """Retest plan (``--plan retest``): only setup, probe, the sweep, the RUN3 failures, the
    combined block (item 12) and the copy, sized to fit a 2 h VM. Reuses the real test commands;
    the sweep is re-tuned to a tighter budget (1 round, 2-request warm-up) so it still measures the
    four grid caps inside a 16 min block, and t2c-prefix drops to 2 reps for its trimmed budget."""
    full = {b.id: b for b in plan_glimmer_test(out)}
    live_file = out / "live.jsonl"
    guard_cfg = str(Path(__file__).resolve().parents[3] / "examples/longrun/guardrails-wonder.json")
    retune = {"t1-sweep": sweep_live_argv(out, live_file, guard_cfg, sweep_budget_s=840, reps=1,
                                          warmup=2, warmup_s=0, grid="375,350,325,300"),
              # the trimmed t2c-prefix budget (7 min) fits 2 reps, not the full-test 3
              "t2c-prefix": ["python", "scripts/prefix_cache_ab.py", "--out",
                             f"{REMOTE_RESULTS}/t2c-prefix", "--reps", "2"]}
    blocks = []
    for bid in RETEST_BLOCKS:
        b = full[bid]
        if bid == "t3-copy":   # the retest's own trimmed combined block (2 reps, 8 min)
            blocks.append(_combined_block(out))   # item 12 runs just before the copy
        kw = {}
        if bid in RETEST_HOURS_MIN:
            kw["hours"] = _m(RETEST_HOURS_MIN[bid])
        if bid in retune:
            kw["live"] = retune[bid]
        blocks.append(replace(b, **kw) if kw else b)
    return blocks


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
    mismatch: list = field(default_factory=list)   # scripted sha256 mismatches after a pull
    copy_mismatched: list = field(default_factory=list)

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
        ok = self.alive()
        if ok:
            self.copy_mismatched = list(self.mismatch)
        return ok

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
    copy_mismatched: list = field(default_factory=list)

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
        # bounded and in its own process group: a hung sftp.get is killed, not left to wedge
        # teardown (RUN3: a pull hung 14+ min). run2_remote enforces its own inner budget too.
        rc = run_child([sys.executable, str(RUN2_REMOTE), "pull", "--host", self.host,
                        "--key", self.key, "--out", str(self.out_dir),
                        "--timeout", str(PULL_TIMEOUT_S)], timeout=PULL_TIMEOUT_S + 120)
        if rc == 0:
            # a copy whose sha256 differs from the VM's is still a copy (the VM must still be
            # deleted), but the run is flagged: see Driver._copy_verify
            try:
                v = json.loads((self.out_dir / "run2" / "pull-verify.json").read_text())
                # a *.log the server is still appending to (vLLM keeps serving until delete)
                # differs by design; any other file that differs is a bad copy of a result
                self.copy_mismatched = [x for x in (v.get("mismatched") or [])
                                        if not str(x).endswith(".log")]
            except (OSError, ValueError):
                self.copy_mismatched = ["pull-verify.json unreadable"]
        return rc == 0

    @property
    def deleted(self) -> bool:
        return self._deleted

    def delete(self) -> None:
        # ctl.py delete = DELETE + verify (GET 404, gone from the list); exit 2 = not confirmed.
        # Retry so a flaky API call does not leave a billing VM behind.
        for _ in range(3):
            if subprocess.run([sys.executable, str(CTL), "delete"]).returncode == 0:
                self._deleted = True
                return
            time.sleep(30)
        print("VM DELETE NOT CONFIRMED after 3 tries: delete it in the E2E console now",
              file=sys.stderr)


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
        # keep the heartbeat fresh while a long block runs (setup 34 min, sweep 35 min), so a
        # stale-heartbeat watchdog never mistakes a healthy block for a hung driver
        import threading
        stop = threading.Event()

        def _beat():
            while not stop.wait(60.0):
                try:
                    self._heartbeat(block=b.id, note="in-block")
                except Exception:  # noqa: BLE001 - a beat must never break the block
                    pass
        t = threading.Thread(target=_beat, daemon=True)
        t.start()
        try:
            return self.run(b, timeout_s) if self._run_arity >= 2 else self.run(b)
        finally:
            stop.set()

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
        bad = list(getattr(self.vm, "copy_mismatched", None) or [])
        if bad:
            body["copy_verify"] = {"status": "FLAGGED", "mismatched": bad}
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
                if b.skip_if_short:
                    # the combined block is the designated give-way: when a slow upstream block ate
                    # the budget, skip it (logged) rather than drop a previously failed block
                    why = f"skipped: time (needs {b.hours * 60:.0f} min, {left * 60:.0f} min left)"
                    self._note(kind="skipped-time", block=b.id, why=why)
                    self.state["blocks"][b.id] = {"status": "skipped", "why": why}
                else:
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
                if copied and getattr(self.vm, "copy_mismatched", None):
                    result["copy_verify"] = {"status": "FLAGGED",
                                             "mismatched": list(self.vm.copy_mismatched)}
                cands = [self.out / "runs" / b.id / "tiers_summary.json",
                         self.out / "pulled" / "run2" / "results" / b.id / "tiers_summary.json"]
                tiers = next((c for c in cands if c.exists()), cands[0])
                if tiers.exists():   # the tier table goes into the block's recorded state
                    try:
                        result["tiers"] = json.loads(tiers.read_text())
                    except (OSError, ValueError) as e:   # a truncated pull never aborts the plan
                        result["tiers_error"] = f"{type(e).__name__}: unreadable tiers_summary.json"
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
        bad = list(getattr(self.vm, "copy_mismatched", None) or [])
        if bad:
            # the final copy differs from the VM's sha256 after a retry: the VM is still deleted
            # (billing stops), but the run is FLAGGED, never reported as a clean success
            self.state["copy_verify"] = {"status": "FLAGGED", "mismatched": bad,
                                         "note": "sha256 of the copy differs from the VM's"}
            self._note(kind="copy-verify-flagged", mismatched=bad[:20])
            if not str(self.state.get("stopped", "")).endswith("FLAGGED"):
                self.state["stopped"] = f"{self.state.get('stopped')}; copy checksum FLAGGED"
        else:
            self.state["copy_verify"] = {"status": "OK"}
        self._save()
        return self.state


DRY_POLICY_NAME = "dry-approval-policy.json"


def write_dry_policy(out: Path) -> Path:
    """The dry sweep's approval policy: a copy of the operator's policy when it is valid (so the
    dry run checks the same thresholds), else a built-in one marked as set by the dry run."""
    dst = out / DRY_POLICY_NAME
    src = Path(APPROVAL_POLICY)
    if src.is_file() and approval_policy_problem(src) is None:
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        dst.write_text(json.dumps({
            "format": "wattnot.auto-approve/1", "enabled": True, "allowed_labels": ["MEASURED"],
            "kinds": {"power_cap": {"min_saving_pct": 3.0, "max_p99_increase_pct": 10.0,
                                    "max_cap_drop_pct": 58.0}},
            "max_per_day": 3, "set_by": "dry-run", "set_at": "1970-01-01T00:00:00Z"}, indent=1),
            encoding="utf-8")
    return dst


def dry_run(out: Path, *, drop: dict | None = None, dead_after: str | None = None,
            include_optional=(), runner=None, timeout_s: float = 900, model: str = "llama",
            max_hours: float = 18.0, plan_name: str = "full") -> dict:  # watchdog hard cap
    out.mkdir(parents=True, exist_ok=True)
    write_dry_policy(out)
    blocks = plan(out, model, plan_name)
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
        best = None
        if any("{BEST_CAP}" in a for a in argv):
            # resolve the best passing cap from the sweep results on the control box; "NONE" makes
            # the combined block skip cleanly when the sweep found no passing cap
            bc = best_cap_from_sweep(out)
            best = str(bc) if bc else "NONE"
        return [a.replace("{HOST}", host).replace("{KEY}", key)
                 .replace("{BEST_CAP}", best) if best is not None
                else a.replace("{HOST}", host).replace("{KEY}", key) for a in argv]

    def run(b: Block, budget_s: float) -> int:
        if b.infra == "setup":
            # own process group so a driver stop kills setup too (RUN3: an orphaned setup kept
            # dialing a deleted VM's IP). run_child returns 124 if it overruns the block budget.
            return run_child([sys.executable, str(RUN2_REMOTE), "setup", "--host", host,
                              "--key", key, "--model", GLIMMER_MODEL, "--moe", "none"],
                             timeout=budget_s)
        if b.infra == "pull":
            if not vm.pull():
                return 1
            return 7 if vm.copy_mismatched else 0   # copied, but sha256 mismatch: block fails
        if b.live is None:
            return 0
        argv = fill(b.live)
        logf = out / "logs" / f"{b.id}.log"
        logf.parent.mkdir(parents=True, exist_ok=True)
        if b.host:
            from wattnot.proof import sshutil
            inner = host_inner(argv)
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


def _live_publisher(env=None):
    """The off-box heartbeat publisher for a live run (layers 4/5 read it), or None when
    WATCHDOG_PUBLISH_REPO / _GIST_ID is not set. Live runs push for real; failures never raise."""
    from wattnot.longrun.watchdog.publisher import publisher_from_env
    pub = publisher_from_env(env, dry_run=False,
                             log=lambda m: print(f"[driver] {m}", file=sys.stderr))
    if pub is not None:
        print(f"[driver] publishing heartbeat off-box to {pub.raw_url() or pub.mode}",
              file=sys.stderr)
    return pub


def live_run(out: Path, *, host: str, key: str = "~/.ssh/e2e_h100", model: str = "glimmer-test",
             max_hours: float | None = None, include_optional=(), plan_name: str = "full") -> dict:
    """Drive a REAL VM (already launched by scripts/e2e/ctl.py). Runs the no-mock preflight first
    and refuses to start if any live input or step is a mock/placeholder/stub/replay. Never
    launches a VM; teardown goes through ctl.py."""
    out.mkdir(parents=True, exist_ok=True)
    from wattnot.longrun import nomock
    report = nomock.audit_plan(model, plan_name=plan_name)
    nomock.write_report(report, out / "nomock-preflight.json")
    if not report["ok"]:
        print("NO-MOCK preflight FAILED; refusing to start the live run:", file=sys.stderr)
        for f in report["findings"]:
            print(f"  - {f['where']}: {f['what']}", file=sys.stderr)
        return {"stopped": "no-mock preflight failed", "nomock": report, "blocks": {}}

    if model == "glimmer-test":
        why = approval_policy_problem(Path(APPROVAL_POLICY))
        if why:
            print(f"APPROVAL POLICY: {why}; refusing to start (set it with: wattnot proposals "
                  f"--dir {POLICY_DIR}/proposals policy set --by <your name> --file {APPROVAL_POLICY})",
                  file=sys.stderr)
            return {"stopped": "approval policy missing", "blocks": {}}
        for w in approval_policy_warnings(Path(APPROVAL_POLICY)):
            print(f"APPROVAL POLICY WARNING: {w}", file=sys.stderr)
    blocks = plan(out, model, plan_name)
    vm = RemoteVm(host=host, key=key, out_dir=out / "pulled")
    default_h = (2.0 if plan_name == "retest" else 3.5) if model == "glimmer-test" else 18.0
    hours = max_hours if max_hours is not None else default_h
    run = _live_runner(out, host, key, vm)

    last = {"inr": None}

    def balance_inr() -> float:
        # an unreachable balance API keeps the last real reading (ctl.py's own watchdog also
        # checks the balance); before any reading, the 24 h launch floor that ctl.py checked
        try:
            last["inr"] = _ctl_balance()
        except Exception as e:  # noqa: BLE001
            print(f"[driver] balance check failed ({type(e).__name__}); using last reading "
                  f"{last['inr']}", file=sys.stderr)
        return last["inr"] if last["inr"] is not None else 9097.0

    d = Driver(out, blocks, vm, label="MEASURED", run=run, max_hours=hours,
               balance_inr=balance_inr, hours_used=lambda: (time.time() - d._launch_wall) / 3600.0,
               include_optional=include_optional, heartbeat_path=out / "heartbeat.json",
               watchdog_margin_h=WATCHDOG_MARGIN_H if model == "glimmer-test" else 0.0,
               publisher=_live_publisher())
    # anchor the clock to the VM's real launch (ctl.py's vm.json), so the driver's deadline is never
    # later than the off-box watchdog's; only ever moves it earlier
    d._launch_wall = min(d._launch_wall, vm_launch_wall(VM_RECORD) or d._launch_wall)

    # a SIGTERM/SIGINT (ctl stop, a kill) must kill any child we spawned (setup / pull) and run
    # teardown, not leave an orphan. Raise KeyboardInterrupt so the handler below runs.
    def _on_signal(signum, _frame):
        kill_child_groups()
        raise KeyboardInterrupt(f"signal {signum}")

    prev = {}
    for s in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(ValueError, OSError):
            prev[s] = signal.signal(s, _on_signal)
    try:
        return d.go()
    except BaseException:
        # a driver crash, Ctrl-C or a stop signal must not leave a billing VM or an orphaned child:
        # kill child groups, copy what we can, then delete
        kill_child_groups()
        if not vm.deleted:
            d._teardown(why="driver crashed", finished=True)
        raise
    finally:
        for s, h in prev.items():
            with contextlib.suppress(ValueError, OSError):
                signal.signal(s, h)


VM_RECORD = Path("/workspace/hackathon/e2e-glimmer-test/vm.json")   # written by scripts/e2e/ctl.py


def vm_launch_wall(path: Path) -> float | None:
    """Epoch seconds of the VM launch from ctl.py's record (local naive ISO), else None (logged)."""
    try:
        import datetime as _dt
        return _dt.datetime.fromisoformat(json.loads(path.read_text())["launch_ts"]).timestamp()
    except Exception as e:  # noqa: BLE001
        print(f"[driver] no VM launch time from {path} ({type(e).__name__}); clock starts now",
              file=sys.stderr)
        return None


# set by the operator before launch (wattnot proposals policy set --by NAME), never by the plan
# persistent per user (never /tmp, never one checkout); WATTNOT_APPROVAL_POLICY overrides
POLICY_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "wattnot"
APPROVAL_POLICY = os.environ.get("WATTNOT_APPROVAL_POLICY") or str(POLICY_DIR / "approval-policy.json")


def approval_policy_problem(path: Path) -> str | None:
    """None if `path` is an enabled approval policy naming who set it and when."""
    from wattnot.approvals import policy as pol
    if not path.is_file():
        return f"{path} not found"
    try:
        p = pol.Policy.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:  # noqa: BLE001
        return f"{path} is not a valid policy ({e})"
    if not (p.enabled and p.set_by and p.set_at):
        return f"{path} must be enabled and name set_by and set_at"
    return None


H100_STOCK_W = 700.0   # H100 SXM default power limit (the E2E H100 card)


def approval_policy_warnings(path: Path, stock_w: float = H100_STOCK_W) -> list[str]:
    """Non-fatal: say so when the policy would decline every cap the Wonder planner can propose
    (300-375 W). Then each measured cap is rejected after the hold and rolled back: the sweep's
    numbers are still recorded, but no change is ever applied by the policy."""
    from wattnot.approvals import policy as pol
    from wattnot.proof.wonder_planner import HI_W, LO_W
    try:
        p = pol.Policy.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001 - approval_policy_problem reports a bad file
        return []
    rules = p.kinds.get("power_cap") or {}
    mx = rules.get("max_cap_drop_pct")
    least = (1 - HI_W / stock_w) * 100   # shallowest cap
    most = (1 - LO_W / stock_w) * 100    # deepest cap
    if isinstance(mx, int | float) and mx < most:
        import math
        scope = "every" if mx < least else "the deeper"
        return [f"max_cap_drop_pct {mx:g}% is below {scope} Wonder cap ({LO_W}-{HI_W} W on a "
                f"{stock_w:g} W card = {least:.1f}-{most:.2f}% drop): the policy will decline "
                f"{scope} cap after the hold and roll it back (numbers still recorded). "
                f"Raise it to {math.ceil(most)} to let the policy approve every cap."]
    return []


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
    ap.add_argument("--plan", choices=["full", "retest"], default="full",
                    help="retest (glimmer-test only): re-run setup, probe, the sweep and the RUN3 "
                         "failures only, sized to a 2 h VM")
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
        st = live_run(a.out, host=host, key=a.key, model=a.model, max_hours=a.max_hours,
                      plan_name=a.plan)
        for k, v in st.get("blocks", {}).items():
            print(f"{k:18} {v.get('status', '?'):8} rc={v.get('rc', '-')} "
                  f"{v.get('wall_s', '')}s {v.get('why', v.get('teardown', ''))}")
        print("stopped:", st.get("stopped"))
        print("copy verify:", (st.get("copy_verify") or {}).get("status", "?"))
        # an early stop (money rule, VM dead) is not a success even if every block that ran passed
        if (st.get("copy_verify") or {}).get("status") == "FLAGGED":
            return 1
        return 0 if str(st.get("stopped", "")).startswith("finished") and all(
            v.get("status") in ("done", "skipped", "dropped")
            for v in st.get("blocks", {}).values()) else 1

    drop = {}
    if a.drop_link:
        k, n = a.drop_link.split(":")
        drop[k] = int(n)
    if a.model != "llama" and a.drop_link == "b2-wonder-sweep:3":
        drop = {"g2-wonder-sweep": 3} if a.model == "glimmer" else {"t1-resume": 2}
    default_h = (2.0 if a.plan == "retest" else 3.5) if a.model == "glimmer-test" else 18.0
    hours = a.max_hours or default_h
    st = dry_run(a.out, drop=drop, model=a.model, max_hours=hours, plan_name=a.plan)
    for k, v in st["blocks"].items():
        print(f"{k:18} {v['status']:8} rc={v.get('rc', '-')} {v.get('wall_s', '')}s "
              f"{v.get('why', v.get('teardown', ''))}")
    print("stopped:", st["stopped"], "| sim:", st["sim"])
    if (st.get("copy_verify") or {}).get("status") == "FLAGGED":
        return 1
    return 0 if all(v["status"] in ("done", "skipped", "dropped")
                    for v in st["blocks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
