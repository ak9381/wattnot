"""A run's checkpoint: what the control machine has finished, so a stopped run can carry on.

RUN2's control session hung after the 450 W cap and the orchestrator had to be stopped; the GPU
host kept going, but the control side had no record of what was done. `Checkpoint` is that record:
one small JSON file, written atomically after every finished stage (and after every measured cap
inside the sweep). `orchestrate --resume` reads it, skips what is marked done, restores what later
stages need (the room scan, the GPU snapshot, the caps measured so far, the planner's pending
proposal) and carries on in the same run id and the same event log.

The file holds no secrets: stage payloads are results (JSON numbers and strings), never keys or
tokens. A checkpoint belongs to one configuration: resuming with different measurement settings
would mix two methods in one result, so a changed fingerprint is refused.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

VERSION = 1
STAGES = ("room_scan", "baseline", "quality_gate", "cap_sweep", "adaptive_topk", "approve",
          "keep_rollback", "room_after", "receipt", "finished")


class CheckpointError(RuntimeError):
    pass


class Checkpoint:
    def __init__(self, path: Path, run_id: str, fingerprint: dict) -> None:
        self.path, self.run_id, self.fingerprint = Path(path), run_id, fingerprint
        self.stages: dict[str, Any] = {}
        self.cmds: dict[str, int] = {}  # GPU-host command key -> output lines already handled
        self.elapsed_s = 0.0
        self.resumes = 0
        self.created = time.time()

    # -- open / create
    @classmethod
    def create(cls, path: Path, run_id: str, fingerprint: dict) -> Checkpoint:
        ck = cls(path, run_id, fingerprint)
        ck.save()
        return ck

    @classmethod
    def load(cls, path: Path, fingerprint: dict | None = None) -> Checkpoint:
        """Read a checkpoint. Refuses a different configuration (`fingerprint`) or version."""
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise CheckpointError(f"cannot read checkpoint {path}: {e}") from e
        if d.get("version") != VERSION:
            raise CheckpointError(f"checkpoint version {d.get('version')} is not {VERSION}")
        if fingerprint is not None and d.get("fingerprint") != fingerprint:
            diff = sorted(k for k in set(fingerprint) | set(d.get("fingerprint", {}))
                          if fingerprint.get(k) != d.get("fingerprint", {}).get(k))
            raise CheckpointError(
                "this checkpoint was made with different settings (" + ", ".join(diff) + "); "
                "resuming would mix two methods in one result. Start a fresh run, or rerun "
                "with the same settings")
        ck = cls(path, d["run_id"], d.get("fingerprint", {}))
        ck.stages = d.get("stages", {})
        ck.cmds = {k: int(v) for k, v in (d.get("cmds") or {}).items()}
        ck.elapsed_s = float(d.get("elapsed_s", 0.0))
        ck.resumes = int(d.get("resumes", 0))
        ck.created = float(d.get("created", time.time()))
        return ck

    # -- stages
    def done(self, stage: str) -> bool:
        return bool((self.stages.get(stage) or {}).get("done"))

    def get(self, stage: str) -> dict:
        return self.stages.get(stage) or {}

    def update(self, stage: str, **payload: Any) -> None:
        """Merge `payload` into a stage (not yet done) and save."""
        self.stages.setdefault(stage, {}).update(payload)
        self.save()

    def mark(self, stage: str, **payload: Any) -> None:
        """Mark a stage done, with what later stages need from it."""
        self.stages.setdefault(stage, {}).update(payload, done=True)
        self.save()

    def set_lines(self, key: str, n: int) -> None:
        """How many output lines of a GPU-host command have been handled (so a re-attach after a
        restart replays those quietly instead of emitting their events twice)."""
        if self.cmds.get(key) != n:
            self.cmds[key] = n
            self.save()

    def last_done(self) -> str | None:
        for s in reversed(STAGES):
            if self.done(s):
                return s
        return None

    # -- persistence (atomic: a crash mid-write leaves the previous file whole)
    def save(self) -> None:
        d = {"version": VERSION, "run_id": self.run_id, "fingerprint": self.fingerprint,
             "created": self.created, "updated": time.time(), "elapsed_s": round(self.elapsed_s, 1),
             "resumes": self.resumes, "cmds": self.cmds, "stages": self.stages}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(d, f, indent=1, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
