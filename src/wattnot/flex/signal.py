"""Grid events for flex mode. Replay only: a file of events, no live feed.

An event asks for a cut in GPU draw for a while:

    {"id": "demo-evt-1", "kind": "shed", "target_pct": 25, "duration_s": 300}

`target_pct` is the cut from the draw measured just before the event. The file's `label`
is carried onto the receipt, so a replayed event is never mistaken for a real one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SIGNAL = Path(__file__).resolve().parents[3] / "data" / "flex" / "grid-event-demo.json"


class SignalError(ValueError):
    pass


@dataclass(frozen=True)
class ShedEvent:
    id: str
    target_pct: float
    duration_s: int
    note: str = ""
    kind: str = "shed"

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "target_pct": self.target_pct,
                "duration_s": self.duration_s, "note": self.note}


@dataclass(frozen=True)
class Signal:
    label: str
    source: str
    events: tuple[ShedEvent, ...]


def parse_event(raw: dict, index: int = 0) -> ShedEvent:
    if not isinstance(raw, dict):
        raise SignalError(f"event {index}: expected an object")
    kind = raw.get("kind", "shed")
    if kind != "shed":
        raise SignalError(f"event {index}: kind must be 'shed', got {kind!r}")
    try:
        pct = float(raw["target_pct"])
        dur = int(raw["duration_s"])
    except (KeyError, TypeError, ValueError):
        raise SignalError(
            f"event {index}: target_pct and duration_s are required numbers") from None
    if not 0 < pct < 100:
        raise SignalError(f"event {index}: target_pct must be between 0 and 100")
    if dur < 10:
        raise SignalError(f"event {index}: duration_s must be at least 10")
    return ShedEvent(str(raw.get("id") or f"evt-{index + 1}"), pct, dur, str(raw.get("note", "")))


def load(path: str | Path | None = None) -> Signal:
    p = Path(path) if path else DEFAULT_SIGNAL
    try:
        doc = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise SignalError(f"cannot read signal file {p}: {e}") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("events"), list) or not doc["events"]:
        raise SignalError("signal file needs a non-empty 'events' list")
    label = str(doc.get("label", "SIMULATED"))
    if label not in ("SIMULATED", "MEASURED"):
        raise SignalError("signal label must be SIMULATED or MEASURED")
    events = tuple(parse_event(e, i) for i, e in enumerate(doc["events"]))
    return Signal(label, str(doc.get("source", "replayed events")), events)
