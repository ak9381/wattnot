"""Sidecar: animate the dashboard's live graph for every non-sweep block of a running long-run.

Polls the driver's driver_state.json and heartbeat.json (read-only) and appends wattnot.event/1
step.started / step.finished lines to live.jsonl, the same shape the driver draft emits. It runs
as its own process, so it can never affect the driver: every error is caught and logged.

Sweep blocks (id containing "sweep") are skipped entirely: orchestrate already writes their
events, so emitting them here would duplicate steps on the graph.

    python scripts/graph_events_sidecar.py OUT_DIR [--interval 2] [--once]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from wattnot.proof.events import Stream

# Same table as wattnot.longrun.driver (first match wins); imported when available.
NODE_KEYWORDS: tuple[tuple[str, tuple[str, str]], ...] = (
    ("setup", ("e2e_rig", "setup")), ("probe", ("e2e_rig", "probe")),
    ("sweep", ("e2e_rig", "sweep")), ("wonder", ("optimizer", "wonder")),
    ("eu", ("eu", "export")), ("eye", ("gates", "eye")), ("spoken", ("gates", "spoken")),
    ("prefix", ("cache", "prefix")), ("flex", ("flex", "event")),
    ("langchain", ("agent", "langchain")), ("agentwaste", ("agent", "waste")),
    ("combined", ("apply", "combined")), ("copy", ("e2e_rig", "copy")),
)
PASSED = {"done", "skipped", "dropped"}
FINAL = {"done", "failed", "lost", "skipped", "dropped"}


try:
    from wattnot.longrun.driver import _NODE_KEYWORDS as NODE_KEYWORDS  # noqa: F811
except Exception:  # an older checkout: keep the local table
    pass


def block_route(block_id: str) -> tuple[str, str]:
    for key, route in NODE_KEYWORDS:
        if key in block_id.lower():
            return route
    return ("workload", "send")


def is_sweep(block_id: str) -> bool:
    return "sweep" in block_id


def iso_now() -> str:
    dt = datetime.now(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def append_line(path: Path, line: str) -> None:
    """One O_APPEND write per line, so concurrent writers never interleave inside a line."""
    data = (line.rstrip("\n") + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


class Sidecar:
    def __init__(self, out: Path, label: str = "MEASURED", run_id: str | None = None):
        self.out, self.live = out, out / "live.jsonl"
        self.run_id = run_id or f"sidecar-{datetime.now(UTC):%Y%m%d_%H%M%S}"
        self.seq = 0
        self.stream = Stream(self._sink, label=label)
        self.open: dict[str, object] = {}   # block id -> open step
        self.attempt: dict[str, object] = {}

    def _sink(self, raw: dict) -> None:
        self.seq += 1
        ev = {"schema_version": "wattnot.event/1", "run_id": self.run_id, "seq": self.seq,
              "ts": iso_now(), "type": raw["type"], "step_id": raw.get("step_id"),
              "parent_step_id": raw.get("parent_step_id"), "data": raw.get("data") or {}}
        append_line(self.live, json.dumps(ev, ensure_ascii=False, separators=(",", ":"), default=str))

    def _start(self, bid: str) -> None:
        if is_sweep(bid) or bid in self.open:
            return
        node, sub = block_route(bid)
        self.open[bid] = self.stream.start(self.stream.sid(bid), node, sub, bid, say=bid)

    def _finish(self, bid: str, status: str, info: dict) -> None:
        st = self.open.pop(bid, None)
        if st is None or is_sweep(bid):
            return
        st.ok = status == "done"
        st.outputs = status if not info.get("note") else f"{status}: {str(info['note'])[:160]}"
        self.stream.finish(st)

    def tick(self) -> None:
        """Finish any open step whose block is no longer the heartbeat's block (or whose attempt
        changed, i.e. a retry), then start a step for the heartbeat's current block."""
        state = read_json(self.out / "driver_state.json")
        hb = read_json(self.out / "heartbeat.json")
        if state.get("graph_events") == "driver":
            # the driver emits its own block steps (wattnot.longrun.driver); doubling them here
            # would draw every step twice, so stand down and close anything still open
            for bid in list(self.open):
                self._finish(bid, ((state.get("blocks") or {}).get(bid) or {}).get("status") or "failed", {})
            return
        blocks = state.get("blocks") or {}
        # The driver beats block=<id>, note=None at the top of every block (also for ones it is
        # about to pass over as done/skipped), then note="in-block" every 60 s while it runs.
        cur = hb.get("block")
        if hb.get("note") != "in-block" and (blocks.get(cur) or {}).get("status") in PASSED:
            cur = None
        for bid in list(self.open):
            info = blocks.get(bid) or {}
            if bid != cur or info.get("attempt") != self.attempt.get(bid):
                self._finish(bid, info.get("status") or "failed", info)
        if cur and cur not in self.open and not is_sweep(cur):
            self._start(cur)
            self.attempt[cur] = (blocks.get(cur) or {}).get("attempt")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    sc = Sidecar(a.out)
    while True:
        try:
            sc.tick()
        except Exception as e:  # never let anything escape
            print(f"sidecar: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        if a.once:
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    raise SystemExit(main())
