"""The proof run as `wattnot.event/1` events, in the shape the dashboard's Architecture map reads.

Every step of the run is a `step.started` ... `step.finished` pair. A step is named
(`room_scan`, `baseline`, `quality_gate`, `cap_sweep`, `adaptive_topk`, `keep_rollback`,
`receipt`) and a sub-step points at its parent with `parent_step_id`. Each step event carries

    node, sub   which box on the Architecture map lights up (ids from the map's own spec)
    label       one line for the narration
    inputs      what went in, as a short string
    outputs, numbers[{label, v, unit, tag}], duration_s, status   on `step.finished`
    partners    who is behind the step (meta, nvidia, e2e, hf, sigstore, oss)

Numbers are tagged MEASURED / ESTIMATE / SIMULATED / MOCK / ADVICE. Nothing is tagged MEASURED
unless it came off the GPU's own counter or a timer in this run.

The GPU host emits "raw" events (no run id, no sequence number) as lines `EVT {...}` on stdout and
into its own file. The control machine reads that stream over SSH, and gives each event its run id,
sequence number and time before writing the one validated `events.jsonl` the dashboard follows.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

LABELS = ("MEASURED", "ESTIMATE", "SIMULATED", "MOCK", "ADVICE", "REPLAY")

# step name -> (node, sub) on the Architecture map, for the step as a whole
STEP_NODE = {
    "room_scan": ("sam", "masks"),
    "baseline": ("workload", "send"),
    "quality_gate": ("gates", "decide"),
    "cap_sweep": ("e2e_rig", "sweep"),
    "adaptive_topk": ("adaptive", "arms"),
    "keep_rollback": ("apply", "keep"),
    "receipt": ("receipts", "sign"),
}
PARTNERS = {
    "sam": ["meta"],
    "agent": ["meta"],
    "workload": ["oss"],
    "telemetry": ["nvidia"],
    "apply": ["nvidia", "e2e"],
    "gates": ["oss"],
    "e2e_rig": ["e2e"],
    "adaptive": ["hf"],
    "receipts": ["sigstore"],
    "optimizer": ["oss"],
    "models": ["meta", "hf"],
}


def num(label: str, v, unit: str = "", tag: str = "MEASURED") -> dict:
    """One number for a step's `numbers` list (tag is dropped when the value is missing)."""
    ok = v is not None
    return {
        "label": label,
        "v": round(v, 4) if isinstance(v, float) else v,
        "unit": unit,
        "tag": tag if ok else None,
    }


Sink = Callable[[dict], None]


def stdout_sink(path: Path | None = None) -> Sink:
    """GPU-host sink: one `EVT {json}` line on stdout (the control machine reads it live) and the
    same line appended to a local file, so a dropped connection loses nothing."""
    lock = threading.Lock()

    def sink(ev: dict) -> None:
        line = json.dumps(ev, ensure_ascii=False, separators=(",", ":"), default=str)
        with lock:
            print("EVT " + line, flush=True)
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")

    return sink


class Stream:
    """Builds events and hands them to a sink. Thread-safe."""

    def __init__(self, sink: Sink, *, label: str = "MEASURED", clock=time.time) -> None:
        self.sink, self.label, self.clock = sink, label, clock
        self._n: dict[str, int] = {}
        self._lock = threading.Lock()

    def emit(
        self, type_: str, data: dict, *, step_id: str | None = None, parent: str | None = None
    ) -> None:
        self.sink(
            {
                "type": type_,
                "step_id": step_id,
                "parent_step_id": parent,
                "t": round(self.clock(), 3),
                "data": data,
            }
        )

    def sid(self, base: str) -> str:
        with self._lock:
            self._n[base] = self._n.get(base, 0) + 1
            n = self._n[base]
        return base if n == 1 else f"{base}#{n}"

    def start(
        self,
        step_id: str,
        node: str,
        sub: str,
        label: str,
        *,
        inputs: str = "",
        parent: str | None = None,
        say: str | None = None,
        top: str | None = None,
    ) -> _Step:
        """Open a step (emit `step.started`). Close it with `finish(st)`. `top` names the run-level
        step (room_scan ...) this one belongs to."""
        st = _Step()
        st.id, st.parent, st.label, st.t0 = step_id, parent, label, time.monotonic()
        st.tag = self.label
        st.base = {
            "node": node,
            "sub": sub,
            "kind": f"{node}.{sub}",
            "partners": PARTNERS.get(node, []),
        }
        if top:
            st.base["step"] = top
        self.emit(
            "step.started",
            {
                **st.base,
                "label": label,
                "inputs": inputs,
                "narration": {"text": say or label, "source": "template"},
            },
            step_id=step_id,
            parent=parent,
        )
        return st

    def reopen(
        self,
        step_id: str,
        node: str,
        sub: str,
        label: str,
        *,
        parent: str | None = None,
        top: str | None = None,
    ) -> _Step:
        """A handle for a step that was already started (in an earlier process of a resumed
        run): same ids and base fields as `start`, but no `step.started` event is emitted, so the
        log has one start and one finish. `duration_s` counts from the resume."""
        st = _Step()
        st.id, st.parent, st.label, st.t0 = step_id, parent, label, time.monotonic()
        st.tag = self.label
        st.base = {
            "node": node,
            "sub": sub,
            "kind": f"{node}.{sub}",
            "partners": PARTNERS.get(node, []),
        }
        if top:
            st.base["step"] = top
        return st

    def finish(self, st: _Step) -> None:
        self.emit(
            "step.finished",
            {
                **st.base,
                "status": "ok" if st.ok else "failed",
                "summary": st.summary or st.label,
                "outputs": st.outputs,
                "duration_s": round(time.monotonic() - st.t0, 2),
                "label": st.tag,
                "numbers": st.numbers,
                **st.extra,
            },
            step_id=st.id,
            parent=st.parent,
        )

    @contextlib.contextmanager
    def step(self, step_id: str, node: str, sub: str, label: str, **kw):
        """`with s.step(...) as st:` ... set `st.outputs`, `st.numbers`, `st.ok`, `st.tag`."""
        st = self.start(step_id, node, sub, label, **kw)
        try:
            yield st
        except BaseException as e:
            st.ok, st.outputs = False, f"{type(e).__name__}: {str(e)[:200]}"
            self.emit("error", {**st.base, "message": st.outputs}, step_id=st.id, parent=st.parent)
            raise
        finally:
            self.finish(st)

    def progress(
        self, step_id: str, node: str, sub: str, pct: int, message: str, parent: str | None = None
    ) -> None:
        self.emit(
            "step.progress",
            {"node": node, "sub": sub, "pct": pct, "message": message},
            step_id=step_id,
            parent=parent,
        )

    def telemetry(self, **kw) -> None:
        self.emit("telemetry.sample", {"gpu_index": 0, "source": "nvml", "label": "MEASURED", **kw})


class _Step:
    id: str
    parent: str | None
    label: str
    t0: float
    base: dict

    def __init__(self) -> None:
        self.ok, self.outputs, self.summary, self.tag = True, "", None, "MEASURED"
        self.numbers: list[dict] = []
        self.extra: dict = {}

    def add(self, label: str, v, unit: str = "", tag: str | None = None) -> None:
        self.numbers.append(num(label, v, unit, tag or self.tag))


class NullStream(Stream):
    """For tests and callers that do not want events."""

    def __init__(self) -> None:
        super().__init__(lambda ev: None)


def read_raw(line: str) -> dict | None:
    """Parse one `EVT {...}` line from the GPU host (None for anything else)."""
    if not line.startswith("EVT "):
        return None
    try:
        ev = json.loads(line[4:])
    except ValueError:
        return None
    return ev if isinstance(ev, dict) and "type" in ev else None


def check_labels(ev: dict) -> list[str]:
    """Problems with the label of an event (used by the tests: a number without a tag, or a tag
    that is not one of the five)."""
    bad = []
    d = ev.get("data", {})
    for n in d.get("numbers", []) or []:
        if n.get("v") is not None and n.get("tag") not in LABELS:
            bad.append(f"{ev['type']}:{n.get('label')} has tag {n.get('tag')!r}")
    if d.get("label") and ev["type"] == "step.finished" and d["label"] not in LABELS:
        bad.append(f"{ev['type']} label {d['label']!r}")
    return bad
