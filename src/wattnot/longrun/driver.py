"""The long-run driver: runs the block plan in order, records each block, never loses results.

    python -m wattnot.longrun.driver --dry-run --out /tmp/longrun-dry      # CPU + mocks, no VM

Per block: money check -> recorder.begin -> run -> (link lost? -> plan_teardown) -> pull ->
recorder.end -> checkpoint. Blocks are dropped from the bottom when the time left cannot cover
them. A restarted driver (--resume) skips blocks already done.

Live mode is NOT wired to a launcher here on purpose: it needs Akash's go, a VM and a provider
client. `VmControl` is the seam: the dry run uses `FakeVm`, the live run will pass an E2E-backed
one (alive / api_state / pull / delete) built from scripts/run2_remote.py and the E2E API.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from wattnot.proof.teardown import plan_teardown

from .recorder import Recorder

INR_PER_H = 451.2          # E2E H100 + GST + disk (MEASURED price list)
INR_PER_USD = 88.0         # ASSUMED
BALANCE_FLOOR_INR = 1500.0  # the balance watchdog floor in long-run-plan.md
LINK_LOST_RCS = (4, 5)     # run2_remote: 4 = link lost mid-setup, 5 = unreachable


@dataclass
class Block:
    id: str
    title: str
    hours: float
    dry: list[str] | None          # argv for the dry run (None = nothing to simulate)
    capture: bool = False
    optional: bool = False         # needs Akash's yes (e.g. the second model)
    min_hours: float | None = None # a trimmed version fits in this much time ...
    trim: list[str] | None = None  # ... with this argv (e.g. fewer items per tier)


def _py(*args: str) -> list[str]:
    return [sys.executable, "-m", *args]


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
        # g6: free slot for the thinking-budget block (designed separately). Adaptive top-k is
        # not in the H100 run: Glimmer is dense, and top-k is planned on Spark via Modal.
        Block("g7-flex", "Flex / load-shed test", 0.75, None),
        Block("g8-buffer", "Buffer for redoing failed blocks", 1.0, None),
    ]


def plan_glimmer_test(out: Path) -> list[Block]:
    """A standalone 1-2 h Glimmer test VM: memory, FP8, timings. Not a measured result."""
    tiers = lambda tag, cfg: _py("wattnot.longrun.harness", "--cap", "350", "--out",  # noqa: E731
                                 str(out / "runs" / tag), "--fake", "--model-config", cfg,
                                 "--per-tier", "4")
    return [
        Block("t0-setup", "Launch, setup, Glimmer weights, probe, -lgc test", 0.75, None),
        Block("t1-bf16", "BF16: KV headroom per tier, thinking budget, one cap timing", 0.5,
              tiers("t1", "muse-glimmer-30b")),
        Block("t2-fp8", "FP8 restart: memory, start time, the same mini tiers", 0.5,
              tiers("t2", "muse-glimmer-30b-fp8")),
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


class Driver:
    def __init__(self, out: Path, blocks: list[Block], vm: VmControl, *, label: str,
                 run: Callable[[Block], int], max_hours: float, balance_inr: Callable[[], float],
                 hours_used: Callable[[], float], capture: bool = False, include_optional=(),
                 sleep=time.sleep, clock=time.monotonic, log=print) -> None:
        self.out, self.blocks, self.vm, self.label = out, blocks, vm, label
        self.run, self.max_hours, self.balance, self.used = run, max_hours, balance_inr, hours_used
        self.capture, self.include = capture, set(include_optional)
        self.sleep, self.clock, self.log = sleep, clock, log
        self.state_path = out / "driver_state.json"
        self.state = (json.loads(self.state_path.read_text()) if self.state_path.exists()
                      else {"blocks": {}, "events": [], "stopped": None})
        self.rec = Recorder(out, out / "live.jsonl", label=label)

    def _save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, default=str))
        os.replace(tmp, self.state_path)

    def _note(self, **kw) -> None:
        kw["h"] = round(self.used(), 2)
        self.state["events"].append(kw)
        self.log(json.dumps(kw, default=str))
        self._save()

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

    def go(self) -> dict:
        for b in self.blocks:
            st = self.state["blocks"].get(b.id, {})
            if st.get("status") in ("done", "skipped", "dropped"):
                continue
            if b.optional and b.id not in self.include:
                self.state["blocks"][b.id] = {"status": "skipped", "why": "needs Akash's yes"}
                self._save()
                continue
            bal = self.balance()
            # time left = the smaller of the hard deadline and what the credit above the floor buys
            left = min(self.max_hours - self.used(), (bal - BALANCE_FLOOR_INR) / INR_PER_H)
            if bal < BALANCE_FLOOR_INR or self.used() >= self.max_hours:
                self._teardown(why=f"before {b.id}", balance_low=bal < BALANCE_FLOOR_INR,
                               hard_deadline_hit=self.used() >= self.max_hours)
                self.state["stopped"] = "money rule"
                break
            if b.hours > left and b.min_hours is not None and b.min_hours <= left:
                self._note(kind="trimmed", block=b.id, hours=b.min_hours, left=round(left, 2))
                b = Block(b.id, b.title + " (trimmed)", b.min_hours, b.trim, b.capture,
                          b.optional)
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
                rc = self.run(b)
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

    def run(b: Block) -> int:
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
                                      timeout=timeout_s).returncode
            except subprocess.TimeoutExpired:
                return 124

    vm = FakeVm(drop=drop or {}, dead_after=dead_after)
    t = {"now": 0.0}

    def sleep(s):
        t["now"] += s

    d = Driver(out, blocks, vm, label="MOCK", run=run, max_hours=max_hours,
               balance_inr=lambda: 9501.60 - sim["h"] * INR_PER_H, hours_used=lambda: sim["h"],
               sleep=sleep, clock=lambda: t["now"], include_optional=include_optional)
    state = d.go()
    state["sim"] = {"hours": sim["h"], "inr": round(sim["h"] * INR_PER_H, 0),
                    "usd": round(sim["h"] * INR_PER_H / INR_PER_USD, 2),
                    "vm_deleted": vm.deleted, "pulls": vm.pulls}
    (out / "driver_state.json").write_text(json.dumps(state, indent=1, default=str))
    return state


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", required=True,
                    help="only the CPU/mock dry run exists; live needs Akash's go")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", choices=["glimmer", "llama", "glimmer-test"], default="glimmer",
                    help="glimmer = primary plan; llama = fallback; glimmer-test = 1-2 h test VM")
    ap.add_argument("--max-hours", type=float, default=None)
    ap.add_argument("--drop-link", default="b2-wonder-sweep:3",
                    help="simulate an SSH drop: block:probes ('' for none)")
    a = ap.parse_args(argv)
    drop = {}
    if a.drop_link:
        k, n = a.drop_link.split(":")
        drop[k] = int(n)
    if a.model != "llama" and a.drop_link == "b2-wonder-sweep:3":
        drop = {"g2-wonder-sweep": 3} if a.model == "glimmer" else {"t1-bf16": 2}
    hours = a.max_hours or (2.0 if a.model == "glimmer-test" else 18.0)
    st = dry_run(a.out, drop=drop, model=a.model, max_hours=hours)
    for k, v in st["blocks"].items():
        print(f"{k:18} {v['status']:8} rc={v.get('rc', '-')} {v.get('wall_s', '')}s "
              f"{v.get('why', v.get('teardown', ''))}")
    print("stopped:", st["stopped"], "| sim:", st["sim"])
    return 0 if all(v["status"] in ("done", "skipped", "dropped")
                    for v in st["blocks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
