"""Run the paid Baseten stage round by round, with screen clips.

For each round and each arm it starts one dashboard server (port 8771) following that arm's live
events file, records /graph.html?live=1 headless (Chrome + Playwright video), then signals the
runner to start the arm. After the round it records the Run matrix page; after the PR round it
records the PR page. Clips: data/baseten-1101/clips/baseten-rNN-graph-{without,with}.mp4 etc.

    PYTHONPATH=.:src python scripts/e2e/baseten_stage.py --rounds r01 r02 r03 --pr-round r03
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src")]
PORT = 8771
CHROME = "/opt/google/chrome/chrome"
CLIPS = ROOT / "data" / "baseten-1101" / "clips"
if "--smoke" in sys.argv:
    CLIPS = ROOT / "data" / "baseten-1101" / "clips-smoke"


# Lesson from the Modal 1:00 AM abort: the dashboard needs uvicorn/fastapi, which may live in a
# different venv than the runner. WN_DASH_PY picks that venv (preflight checks it imports).
DASH_PY = os.environ.get("WN_DASH_PY", os.environ.get("WN_PY", "/home/box/.local/bin/python"))


def stop_runner(runner) -> None:
    runner.terminate()
    try:
        runner.wait(timeout=60)
    except subprocess.TimeoutExpired:
        runner.kill()


def server(events: Path):
    health = f"http://127.0.0.1:{PORT}/api/dash/health"
    p = subprocess.Popen([DASH_PY, "-m", "wattnot.dash", "--port", str(PORT), "--events", str(events)],
                         cwd=ROOT, env={**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT/'src'}"},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    last = ""
    for _ in range(60):  # ~30 s
        rc = p.poll()
        if rc is not None:  # the server exited before it was ever ready: say so, don't keep polling
            raise RuntimeError(f"dashboard process exited (rc {rc}) before {health} answered")
        try:
            with urllib.request.urlopen(health, timeout=2) as r:
                if r.status == 200:  # ready only on a real 200 from the real health URL
                    return p
                last = f"HTTP {r.status}"
        except OSError as e:  # not up yet (connection refused) or a transient read error
            last = f"{type(e).__name__}: {getattr(e, 'reason', e)}"
        time.sleep(0.5)
    p.kill()
    raise RuntimeError(f"dashboard on port {PORT} not ready after 30s polling {health}; last: {last or 'no response'}")


def to_mp4(webm: Path, mp4: Path):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(mp4)], check=True)
    webm.unlink(missing_ok=True)


def record(url: str, out_mp4: Path, until, max_s: float, tail_s: float = 4.0, ready=None):
    from playwright.sync_api import sync_playwright
    tmp = CLIPS / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx = b.new_context(viewport={"width": 1600, "height": 1300}, record_video_dir=str(tmp),
                            record_video_size={"width": 1600, "height": 1300})
        pg = ctx.new_page()
        pg.goto(url)
        pg.wait_for_timeout(3000)
        if ready:
            ready()
        t0 = time.time()
        while time.time() - t0 < max_s and not until():
            pg.wait_for_timeout(1000)
        pg.wait_for_timeout(int(tail_s * 1000))
        v = pg.video.path()
        ctx.close()
        b.close()
    to_mp4(Path(v), out_mp4)


def finished(events: Path, arm: str):
    def f():
        if not events.exists():
            return False
        return any('"run.finished"' in ln and f'"arm": "{arm}"' in ln for ln in events.read_text().splitlines())
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", nargs="+", default=["r01", "r02", "r03"])
    ap.add_argument("--pr-round", default="r03")
    ap.add_argument("--smoke", action="store_true", help="1-item smoke of the whole flow (no PR)")
    ap.add_argument("--sets", nargs="+", default=[], help="datasets; public round: --sets gsm8k humaneval")
    a = ap.parse_args()
    from wattnot.launch_check import run as preflight   # $0, no network: every Modal lesson first
    pf = preflight("baseten")
    if not pf["ok"]:
        for c in pf["checks"]:
            if not c["ok"]:
                print(f"[preflight] FAIL {c['id']}: {c['detail']}", flush=True)
        print("[stage] preflight failed; nothing paid was started", flush=True)
        return 3
    CLIPS.mkdir(parents=True, exist_ok=True)
    summary = []
    for rnd in a.rounds:
        live = ROOT / "data" / "baseten-1101" / rnd / "live"
        if live.exists():
            shutil.rmtree(live)
        live.mkdir(parents=True)
        cmd = [os.environ.get("WN_PY", "/home/box/.local/bin/python"), "scripts/e2e/baseten_round.py", "--round", rnd, "--mode", "smoke" if a.smoke else "live",
               "--events-dir", str(live), "--open-pr"] + (["--pr"] if rnd == a.pr_round and not a.smoke else []) + (["--limit", "1"] if a.smoke else []) + (["--sets", *a.sets] if a.sets else [])
        logf = open(ROOT / "data" / "baseten-1101" / f"{rnd}.log", "w")
        runner = subprocess.Popen(cmd, cwd=ROOT, env={**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT/'src'}"},
                                  stdout=logf, stderr=subprocess.STDOUT)
        try:
            for arm, tag in (("without_wattnot", "without"), ("with_wattnot", "with")):
                ev = live / f"events-{arm}.jsonl"
                ev.touch()
                srv = server(ev)
                try:
                    done = finished(ev, arm)
                    record(f"http://127.0.0.1:{PORT}/graph.html?live=1", CLIPS / f"baseten-{rnd}-graph-{tag}.mp4",
                           until=lambda: done() or runner.poll() is not None, max_s=3 * 3600,
                           ready=lambda: (live / f"go-{arm}").touch())
                finally:
                    srv.send_signal(signal.SIGTERM)
                    srv.wait(timeout=20)
                if runner.poll() is not None and not done():
                    break
        except BaseException:
            # never leave a paid runner going without its recorder (Modal 1:00 AM lesson)
            stop_runner(runner)
            raise
        rc = runner.wait()
        logf.close()
        # dashboard clip: the run matrix after the round (main dashboard view of the round)
        srv = server(live / "events-with_wattnot.jsonl")
        try:
            t = time.time()
            record(f"http://127.0.0.1:{PORT}/matrix.html", CLIPS / f"baseten-{rnd}-matrix.mp4",
                   until=lambda: time.time() - t > 12, max_s=15)
        finally:
            srv.send_signal(signal.SIGTERM)
            srv.wait(timeout=20)
        summary.append({"round": rnd, "rc": rc})
        print(json.dumps(summary[-1]), flush=True)
        if rc != 0:
            print(f"[stage] {rnd} did not finish cleanly (rc {rc}); stopping the stage here", flush=True)
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
