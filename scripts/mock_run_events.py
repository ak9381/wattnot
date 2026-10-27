"""Write a MOCK run stream that drives the live run view without a GPU.

It takes the planned replay events and relabels them: every MEASURED label becomes
MOCK, telemetry comes from a fake meter, run.started says mode "mock", and the room
scan rides in run.started.data.room. Nothing in the file is a measurement.

    python scripts/mock_run_events.py OUT.jsonl [--model glimmer] [--speed 4] [--instant]

Then:  wattnot dash --events OUT.jsonl   (the Architecture tab shows it as MOCK, live)
The file is appended to in real time (the recorded gaps divided by --speed) so the page
follows it as it grows; --instant writes it all at once.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wattnot.dash import runplan  # noqa: E402


def _is_label_key(k: str) -> bool:
    return k in ("label", "tag") or k.endswith("_label")


def _mock(x):
    if isinstance(x, dict):
        return {k: "MOCK" if _is_label_key(k) and v == "MEASURED" else _mock(v)
                for k, v in x.items()}
    if isinstance(x, list):
        return [_mock(v) for v in x]
    return x.replace("MEASURED", "MOCK") if isinstance(x, str) else x


_NOTE = "This stream comes from a mock run and a fake meter. Nothing in it is measured."


def events(model: str):
    bundle = json.loads((ROOT / "data" / "dash" / "story.json").read_text())
    p = runplan.plan(bundle, {"model": model, "room": "example"})
    room = p["room"] and {k: p["room"][k] for k in ("scan", "after", "target", "pick")}
    host = {"provider": "mock", "gpu": "fake GPU", "gpu_index": 0}
    out = []
    for i, f in enumerate(p["events"]):
        ev = _mock(f["event"])
        d = ev.get("data") or {}
        if ev["type"] == "run.started":
            d.pop("replay", None)
            d.update(mode="mock", label="MOCK", source="mock backend, fake meter", host=host,
                     room=room, meter={"kind": "fake", "label": "MOCK"},
                     narration={"text": _NOTE, "source": "template"})
        if ev["type"] == "telemetry.sample":
            w = d.get("power_w", 400) * (1 + 0.01 * ((i % 5) - 2))
            d.update(source="fake-meter", label="MOCK", power_w=round(w, 1))
        ev["data"] = d
        out.append((f["t_ms"], ev))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path)
    ap.add_argument("--model", default="glimmer")
    ap.add_argument("--speed", type=float, default=4.0)
    ap.add_argument("--instant", action="store_true")
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("")
    prev = 0
    for t, ev in events(a.model):
        if not a.instant:
            time.sleep(max(0, (t - prev) / 1000 / a.speed))
        prev = t
        with a.out.open("a") as fh:
            fh.write(json.dumps(ev, separators=(",", ":")) + "\n")
    print(f"wrote {a.out} (MOCK)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
