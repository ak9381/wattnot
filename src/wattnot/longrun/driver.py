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
BALANCE_FLOOR_INR = 450.0  # stop with about 1 h of credit left
LINK_LOST_RCS = (4, 5)     # run2_remote: 4 = link lost mid-setup, 5 = unreachable


@dataclass
class Block:
    id: str
    title: str
    hours: float
    dry: list[str] | None          # argv for the dry run (None = nothing to simulate)
    capture: bool = False
    optional: bool = False         # needs Akash's yes (e.g. the second model)


def _py(*args: str) -> list[str]:
    return [sys.executable, "-m", *args]


def plan(out: Path) -> list[Block]:
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
        Block("b7-topk", "Adaptive top-k (Granite)", 1.0, reh("b7")),
        Block("b8-spark-open", "Spark Open 816B on one GPU", 3.5, None),
        Block("b9-buffer", "Buffer for redoing failed blocks", 1.5, None),
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

    def go(self) -> dict:
        for b in self.blocks:
            st = self.state["blocks"].get(b.id, {})
            if st.get("status") in ("done", "skipped", "dropped"):
                continue
            if b.optional and b.id not in self.include:
                self.state["blocks"][b.id] = {"status": "skipped", "why": "needs Akash's yes"}
                self._save()
                continue
            bal, left = self.balance(), self.max_hours - self.used()
            if bal < BALANCE_FLOOR_INR or left <= 0:
                self._teardown(why=f"before {b.id}", balance_low=bal < BALANCE_FLOOR_INR,
                               hard_deadline_hit=left <= 0)
                self.state["stopped"] = "money rule"
                break
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
                self.rec.end(result)
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
            include_optional=(), runner=None, timeout_s: float = 900,
            max_hours: float = 18.9) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    blocks = plan(out)
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
    ap.add_argument("--drop-link", default="b2-wonder-sweep:3",
                    help="simulate an SSH drop: block:probes ('' for none)")
    a = ap.parse_args(argv)
    drop = {}
    if a.drop_link:
        k, n = a.drop_link.split(":")
        drop[k] = int(n)
    st = dry_run(a.out, drop=drop)
    for k, v in st["blocks"].items():
        print(f"{k:18} {v['status']:8} rc={v.get('rc', '-')} {v.get('wall_s', '')}s "
              f"{v.get('why', v.get('teardown', ''))}")
    print("stopped:", st["stopped"], "| sim:", st["sim"])
    return 0 if all(v["status"] in ("done", "skipped", "dropped")
                    for v in st["blocks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
