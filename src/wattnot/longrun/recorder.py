"""Record every long-run block so any of them can be replayed as video footage.

Per block, under <out>/recordings/<block_id>/:

- timeline.jsonl  the block's slice of the live event stream (wattnot.event/1). The run screen
                  replays it as-is: web/run/index.html?src=<path>  (scrub, speed 0.5-4x)
- state.json      a snapshot at block end: last event per step, counts, first/last timestamps,
                  plus any block result (e.g. the tiers summary) the driver hands in
- meta.json       block id, label (MEASURED / MOCK), wall-clock start/end, event range
- screen.mp4      optional: a capture of the run screen (Xvfb + Chrome kiosk + ffmpeg x11grab),
                  only for blocks where capture is switched on (the camera block)

Recording never touches the GPU or the VM; it only reads the local live file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


def _read_from(path: Path, offset: int) -> tuple[list[str], int]:
    if not path.exists():
        return [], offset
    with path.open("rb") as f:
        f.seek(offset)
        data = f.read()
    # keep a trailing half-written line for next time
    cut = data.rfind(b"\n") + 1
    lines = [ln for ln in data[:cut].decode("utf-8", "replace").splitlines() if ln.strip()]
    return lines, offset + cut


def snapshot(lines: list[str]) -> dict:
    steps: dict[str, dict] = {}
    types: dict[str, int] = {}
    first = last = None
    for ln in lines:
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        types[e.get("type", "?")] = types.get(e.get("type", "?"), 0) + 1
        first = first or e.get("ts")
        last = e.get("ts") or last
        sid = e.get("step_id")
        if sid:
            steps[sid] = {"type": e.get("type"), "ts": e.get("ts"),
                          "status": (e.get("data") or {}).get("status")}
    return {"events": len(lines), "types": types, "first_ts": first, "last_ts": last,
            "steps": steps}


@dataclass
class Capture:
    """Xvfb + Chrome (kiosk) + ffmpeg x11grab. Started and stopped around one block."""

    url: str
    out: Path
    display: str = ":97"
    size: str = "1920x1080"
    fps: int = 30
    procs: list = field(default_factory=list)

    @staticmethod
    def available() -> str | None:
        """None when it can run; otherwise what is missing."""
        missing = [b for b in ("Xvfb", "ffmpeg") if not shutil.which(b)]
        if not (shutil.which("google-chrome") or shutil.which("chromium")):
            missing.append("chrome")
        return ", ".join(missing) or None

    def commands(self) -> list[list[str]]:
        w, h = self.size.split("x")
        chrome = shutil.which("google-chrome") or shutil.which("chromium") or "google-chrome"
        return [
            ["Xvfb", self.display, "-screen", "0", f"{w}x{h}x24", "-nolisten", "tcp"],
            [chrome, "--kiosk", "--no-first-run", "--disable-gpu", "--no-default-browser-check",
             f"--window-size={w},{h}", "--user-data-dir=/tmp/wattnot-capture-profile", self.url],
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-video_size", self.size,
             "-framerate", str(self.fps), "-i", self.display, "-c:v", "libx264",
             "-preset", "veryfast", "-pix_fmt", "yuv420p", str(self.out)],
        ]

    def start(self, popen=subprocess.Popen, sleep=time.sleep) -> None:
        env = dict(os.environ, DISPLAY=self.display)
        self.out.parent.mkdir(parents=True, exist_ok=True)
        for i, cmd in enumerate(self.commands()):
            self.procs.append(popen(cmd, env=env, stdin=subprocess.PIPE if i == 2 else None,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            sleep(2 if i < 2 else 0)

    def stop(self) -> None:
        # ffmpeg first, with 'q' so it writes a clean mp4 trailer
        for p in reversed(self.procs):
            try:
                if p.stdin:
                    p.stdin.write(b"q")
                    p.stdin.flush()
                    p.wait(timeout=10)
                else:
                    p.terminate()
                    p.wait(timeout=10)
            except Exception:  # noqa: BLE001
                p.kill()
        self.procs.clear()


class Recorder:
    def __init__(self, out: Path, live_file: Path, *, label: str,
                 dashboard: str = "http://127.0.0.1:8781/web/run/index.html") -> None:
        self.root, self.live, self.label, self.dashboard = out / "recordings", live_file, label, \
            dashboard
        self.offset = live_file.stat().st_size if live_file.exists() else 0
        self.block: str | None = None
        self.capture: Capture | None = None
        self.t0 = 0.0
        self.start_offset = 0

    def _head(self) -> bytes:
        if not self.live.exists():
            return b""
        with self.live.open("rb") as f:
            return f.readline()   # the first event: a rewrite starts with a new one

    def begin(self, block_id: str, *, capture: bool = False, popen=subprocess.Popen,
              sleep=time.sleep) -> dict:
        size = self.live.stat().st_size if self.live.exists() else 0
        self.block, self.t0 = block_id, time.time()
        self.start_offset = min(self.offset, size)
        self.head = self._head()
        d = self.root / block_id
        d.mkdir(parents=True, exist_ok=True)
        info = {"capture": False}
        if capture:
            why = Capture.available()
            if why:
                info["capture_skipped"] = f"missing: {why}"
            else:
                self.capture = Capture(f"{self.dashboard}?src=/{self.live}", d / "screen.mp4")
                self.capture.start(popen=popen, sleep=sleep)
                info["capture"] = True
        return info

    def end(self, result: dict | None = None) -> Path:
        assert self.block, "begin() first"
        d = self.root / self.block
        if self.capture:
            self.capture.stop()
            self.capture = None
        size = self.live.stat().st_size if self.live.exists() else 0
        if size < self.start_offset or self._head() != self.head:
            # the run rewrote the file (e.g. --fresh): take it all
            self.start_offset = 0
        lines, self.offset = _read_from(self.live, self.start_offset)
        (d / "timeline.jsonl").write_text("".join(ln + "\n" for ln in lines))
        st = snapshot(lines)
        if result is not None:
            st["result"] = result
        (d / "state.json").write_text(json.dumps(st, indent=1, default=str))
        (d / "meta.json").write_text(json.dumps({
            "block": self.block, "label": self.label, "started": self.t0, "ended": time.time(),
            "byte_range": [self.start_offset, self.offset], "events": len(lines),
            "replay": f"web/run/index.html?src=/{d / 'timeline.jsonl'}",
            "screen": "screen.mp4" if (d / "screen.mp4").exists() else None}, indent=1))
        self.block = None
        return d


def replay(timeline: Path, sink: Path, *, speed: float = 1.0, sleep=time.sleep,
           max_gap_s: float = 5.0) -> int:
    """Re-emit a recorded block into a live file with its original pacing (gaps capped)."""
    from datetime import datetime
    n, prev = 0, None
    with sink.open("a") as out:
        for ln in timeline.read_text().splitlines():
            if not ln.strip():
                continue
            ts = json.loads(ln).get("ts")
            t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() if ts else None
            if prev is not None and t is not None:
                sleep(min(max_gap_s, max(0.0, t - prev)) / speed)
            prev = t if t is not None else prev
            out.write(ln + "\n")
            out.flush()
            n += 1
    return n
