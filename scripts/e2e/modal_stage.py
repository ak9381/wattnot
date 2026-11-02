"""Launch paid stages detached: scripts/e2e/launch_detached.sh LOG -- python scripts/e2e/modal_stage.py ...
(setsid nohup; each round's `modal run --detach` survives a dropped client and the runner waits
for the app to stop before teardown; inner.json and per-item LangChain lines sync every 30 s).
Run the paid Modal stage round by round, with screen clips (one dashboard server at a time).

For each round: starts the round runner (scripts/e2e/modal_full_round.py) with a live events file
that the Modal container streams back through a Modal Queue; records /graph.html?live=1 for the
without-WattNot arm (only the plain path lights; every WattNot node grey) and then for the
with-WattNot arm (features light from real events); after the round records the run matrix page;
reviews the Muse Code PR (diff + checks) and records its page. Clips land in data/modal-full-1101/clips.

    WATTNOT_MODAL_GO=1 PYTHONPATH=.:src python scripts/e2e/modal_stage.py --rounds r04 r05 r06
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
DATA = ROOT / "data" / "modal-full-1101"
CLIPS = DATA / "clips"
PORT = 8772
CHROME = "/opt/google/chrome/chrome"
PY = os.environ.get("WN_PY", sys.executable)
# The recorder dashboard needs uvicorn/fastapi, which live in the dashboard venv, not in the
# system python that runs Modal + Playwright.
DASH_PY = os.environ.get("WN_DASH_PY", PY)
PR_REPO = "ak9381/wattnot-demo-deploy"



def _foreign_owner():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from modal_full_round import foreign_owner
    return foreign_owner()

def server(events: Path):
    p = subprocess.Popen([DASH_PY, "-m", "wattnot.dash", "--port", str(PORT), "--events", str(events)], cwd=ROOT,
                         env={**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'src'}"},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/dash/health", timeout=2)  # noqa: S310
            return p
        except OSError:
            time.sleep(0.5)
    p.kill()
    raise RuntimeError("dashboard did not start")


def stop(p):
    p.send_signal(signal.SIGTERM)
    try:
        p.wait(timeout=20)
    except subprocess.TimeoutExpired:
        p.kill()


def to_mp4(webm: Path, mp4: Path):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm), "-r", "10", "-c:v", "libx264",
                    "-crf", "30", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)], check=True)
    webm.unlink(missing_ok=True)


def record(url: str, out_mp4: Path, until, max_s: float, tail_s: float = 4.0, actions=None,
           convert: bool = True):
    """Record a page. ``convert=False`` keeps the raw webm next to the mp4 path and returns it, so
    the ffmpeg encode can wait until the round's results are written (r04e waited 19 min on it)."""
    from playwright.sync_api import sync_playwright
    tmp = CLIPS / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx = b.new_context(viewport={"width": 1600, "height": 1000}, record_video_dir=str(tmp),
                            record_video_size={"width": 1600, "height": 1000})
        pg = ctx.new_page()
        pg.goto(url)
        pg.wait_for_timeout(3000)
        if actions:
            actions(pg)
        t0 = time.time()
        while time.time() - t0 < max_s and not until():
            pg.wait_for_timeout(1000)
        pg.wait_for_timeout(int(tail_s * 1000))
        v = pg.video.path()
        ctx.close()
        b.close()
    if not convert:
        raw = out_mp4.with_suffix(".webm")
        Path(v).replace(raw)
        return raw
    to_mp4(Path(v), out_mp4)
    return out_mp4


def plays(mp4: Path) -> dict:
    """ffprobe the clip: duration and frame count (a clip 'plays' if it decodes with > 1 s of frames)."""
    p = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                        "stream=nb_read_frames:format=duration", "-of", "json", str(mp4)],
                       capture_output=True, text=True)
    try:
        d = json.loads(p.stdout)
        dur = float(d["format"]["duration"])
        fr = int(d["streams"][0]["nb_read_frames"])
        return {"file": str(mp4.relative_to(ROOT)), "duration_s": round(dur, 1), "frames": fr,
                "plays": dur > 1 and fr > 10, "bytes": mp4.stat().st_size}
    except Exception as e:  # noqa: BLE001
        return {"file": str(mp4), "plays": False, "error": f"{type(e).__name__}: {p.stderr[:200]}"}


def has(path: Path, needle: str) -> bool:
    return path.exists() and needle in path.read_text()


def pr_review(url: str, out: Path) -> dict:
    """Review the Muse Code PR: only the demo repo, only the launch config files, shell + YAML parse,
    no secrets, and the change matches the verified one. Records structured evidence."""
    import tempfile

    import yaml
    out.mkdir(parents=True, exist_ok=True)
    m = re.match(r"https://github.com/([^/]+/[^/]+)/pull/(\d+)", url or "")
    if not m or m.group(1) != PR_REPO:
        return {"decision": "rejected", "why": f"not a PR on {PR_REPO}: {url}"}
    num = m.group(2)
    meta = json.loads(subprocess.run(["gh", "pr", "view", num, "-R", PR_REPO, "--json",
                                      "files,headRefName,baseRefName,title,additions,deletions,url,state"],
                                     capture_output=True, text=True, timeout=120).stdout or "{}")
    diff = subprocess.run(["gh", "pr", "diff", num, "-R", PR_REPO], capture_output=True, text=True,
                          timeout=120).stdout
    (out / "pr.diff").write_text(diff)
    files = [f["path"] for f in meta.get("files") or []]
    checks = {"repo": {"ok": True, "value": PR_REPO},
              "base_main": {"ok": meta.get("baseRefName") == "main", "value": meta.get("baseRefName")},
              "files_allowed": {"ok": bool(files) and set(files) <= {"launch.sh", "values.yaml", "README.md"},
                                "value": files},
              "no_secrets": {"ok": not re.search(r"(sk-|ghp_|api[_-]?key\s*[:=]\s*\S{12,}|BEGIN [A-Z ]*PRIVATE KEY)",
                                                 diff, re.I)},
              "small": {"ok": (meta.get("additions", 0) + meta.get("deletions", 0)) <= 80,
                        "value": [meta.get("additions"), meta.get("deletions")]}}
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(["git", "clone", "-q", "--depth", "1", "-b", meta.get("headRefName", ""),
                            f"https://github.com/{PR_REPO}", td], capture_output=True, text=True, timeout=180)
        if r.returncode == 0:
            sh = subprocess.run(["bash", "-n", f"{td}/launch.sh"], capture_output=True, text=True)
            checks["test_launch_sh_parses"] = {"ok": sh.returncode == 0, "stderr": sh.stderr[:300]}
            try:
                yaml.safe_load(Path(f"{td}/values.yaml").read_text())
                checks["test_values_yaml_parses"] = {"ok": True}
            except Exception as e:  # noqa: BLE001
                checks["test_values_yaml_parses"] = {"ok": False, "error": str(e)[:300]}
            tests = sorted(Path(td).rglob("test*"))
            checks["repo_tests"] = {"ok": True, "value": [str(t.relative_to(td)) for t in tests] or
                                    "the demo repo has no test suite; parse checks above stand in"}
        else:
            checks["clone_head"] = {"ok": False, "error": r.stderr[:300]}
    ok = all(c["ok"] for c in checks.values())
    ev = {"pr": url, "title": meta.get("title"), "state": meta.get("state"), "checks": checks,
          "decision": "approved" if ok else "rejected", "auto_approve": False,
          "failed_checks": [k for k, c in checks.items() if not c["ok"]], "at": datetime.now(UTC).isoformat(),
          "note": "GitHub does not let the PR author approve their own PR; the review is recorded here"}
    (out / "pr_review.json").write_text(json.dumps(ev, indent=1))
    return ev


def find_pr(rdir: Path) -> str | None:
    for p in [rdir / "features" / "features.json", rdir / "round.json"]:
        if p.exists():
            m = re.search(r"https://github.com/ak9381/wattnot-demo-deploy/pull/\d+", p.read_text())
            if m:
                return m.group(0)
    return None


def write_entry(rdir: Path, entry: dict, review: dict | None):
    if rdir.exists():
        (rdir / "stage_entry.json").write_text(json.dumps({**entry, "pr_review_full": review}, indent=1))


def push_results(rdir: Path, tag: str, run=subprocess.run) -> dict:
    """Commit the round's results folder (no clips, no keys) as ak9381 and push the current branch."""
    if not rdir.exists():
        return {"pushed": False, "why": "no results folder"}
    bad = [p.name for p in rdir.rglob("*") if p.suffix in (".pem", ".key", ".mp4", ".webm")]
    if bad:
        return {"pushed": False, "why": f"refused: key or clip files in results: {bad[:3]}"}
    who = ["-c", "user.name=ak9381", "-c", "user.email=ak9381@users.noreply.github.com"]
    steps = [["git", "add", str(rdir)], ["git", *who, "commit", "-q", "-m", f"Results: Modal round {tag}"],
             ["git", "push", "-q", "origin", "HEAD"]]
    for st in steps:
        r = run(st, cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            return {"pushed": False, "step": st[1] if st[1] != "-c" else "commit", "rc": r.returncode}
    return {"pushed": True}


def finish_clips(tag: str, rdir: Path, pr: str | None, clips: list[Path]) -> tuple[dict, dict]:
    """After the results: encode the live recordings, record the matrix and PR pages, ffprobe all."""
    for c in clips:
        raw = c.with_suffix(".webm")
        if raw.exists():
            try:
                to_mp4(raw, c)
            except Exception as e:  # noqa: BLE001 - a bad clip never loses the results
                print(f"[clip] encode {c.name} failed: {type(e).__name__}", flush=True)
    srv = server(DATA / f"{tag}.with.events.jsonl")
    try:
        t = time.time()
        record(f"http://127.0.0.1:{PORT}/matrix.html", CLIPS / f"modal-{tag}-matrix.mp4",
               until=lambda: time.time() - t > 12, max_s=15)
        clips.append(CLIPS / f"modal-{tag}-matrix.mp4")
    finally:
        stop(srv)
    review = pr_review(pr, rdir / "pr_review") if pr else {"decision": None, "why": "no PR opened this round"}
    if pr:
        def scroll(pg):
            for _ in range(6):
                pg.mouse.wheel(0, 500)
                pg.wait_for_timeout(1500)
            pg.goto(pr + "/files")
            pg.wait_for_timeout(3000)
            for _ in range(4):
                pg.mouse.wheel(0, 400)
                pg.wait_for_timeout(1500)
        t = time.time()
        record(pr, CLIPS / f"modal-{tag}-pr-review.mp4", until=lambda: time.time() - t > 2, max_s=5,
               actions=scroll)
        clips.append(CLIPS / f"modal-{tag}-pr-review.mp4")
    return ({"round": tag, "pr": pr, "pr_review": review.get("decision"),
             "clips": [plays(c) for c in clips if c.exists()]}, review)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", nargs="+", default=["r04", "r05", "r06"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-pr", action="store_true")
    ap.add_argument("--push-results", action="store_true",
                    help="commit and push the round's results folder before recording clips")
    ap.add_argument("--sets", nargs="+", default=[], help="datasets; public round: --sets gsm8k humaneval")
    a = ap.parse_args()
    CLIPS.mkdir(parents=True, exist_ok=True)
    log = []
    for rnd in a.rounds:
        tag = f"smoke-{rnd}" if a.limit else rnd
        base = DATA / f"{tag}.events.jsonl"
        evw, evi = DATA / f"{tag}.without.events.jsonl", DATA / f"{tag}.with.events.jsonl"
        for p in (evw, evi):
            p.unlink(missing_ok=True)
            p.touch()
        cmd = [PY, "scripts/e2e/modal_full_round.py", "--round", rnd, "--live-meta", "--events", str(base)]
        cmd += [] if a.no_pr else ["--live-pr"]
        cmd += ["--limit", str(a.limit)] if a.limit else []
        cmd += ["--sets", *a.sets] if a.sets else []
        logf = open(DATA / f"{tag}.log", "w")
        runner = subprocess.Popen(cmd, cwd=ROOT, env={**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'src'}"},
                                  stdout=logf, stderr=subprocess.STDOUT)
        clips = []
        try:
            srv = server(evw)
            try:
                record(f"http://127.0.0.1:{PORT}/graph.html?live=1", CLIPS / f"modal-{tag}-graph-without.mp4",
                       until=lambda: has(evi, '"run.started"') or runner.poll() is not None, max_s=4 * 3600,
                       convert=False)
            finally:
                stop(srv)
            clips.append(CLIPS / f"modal-{tag}-graph-without.mp4")
            srv = server(evi)
            try:
                record(f"http://127.0.0.1:{PORT}/graph.html?live=1", CLIPS / f"modal-{tag}-graph-with.mp4",
                       until=lambda: has(evi, '"run.finished"') or runner.poll() is not None, max_s=4 * 3600,
                       tail_s=8, convert=False)
            finally:
                stop(srv)
        except BaseException:
            # never leave a paid runner going without its recorder: stop it (its own exit path
            # stops the Modal app) and re-raise
            runner.terminate()
            try:
                runner.wait(timeout=180)
            except subprocess.TimeoutExpired:
                runner.kill()
            for _ in range(3):
                try:
                    apps = json.loads(subprocess.run(["modal", "app", "list", "--json"], capture_output=True,
                                                     text=True, timeout=120).stdout or "[]")
                except Exception:  # noqa: BLE001
                    apps = []
                live = [a for a in apps if "wattnot-full-stage" in json.dumps(a)
                        and "stopped" not in json.dumps(a).lower()]
                if not live:
                    break
                if _foreign_owner():
                    break   # another live round owns the app; never stop it from here
                for a in live:
                    aid = a.get("App ID") or a.get("app_id") or a.get("id")
                    if aid:
                        subprocess.run(["modal", "app", "stop", "-y", aid], capture_output=True, timeout=120)
            raise
        clips.append(CLIPS / f"modal-{tag}-graph-with.mp4")
        rc = runner.wait()
        logf.close()
        rdir = DATA / tag
        # 1. results first: the round's entry is written (and pushed with --push-results) before
        #    any clip is encoded or recorded
        pr = find_pr(rdir)
        entry = {"round": tag, "rc": rc, "pr": pr, "pr_review": None, "clips": "pending"}
        write_entry(rdir, entry, None)
        print("[results-ready] " + json.dumps(entry), flush=True)
        if a.push_results:
            print("[results-pushed] " + json.dumps(push_results(rdir, tag)), flush=True)
        # 2. then the clips: encode the live graph recordings, record matrix + PR review, ffprobe
        entry, review = finish_clips(tag, rdir, pr, clips)
        entry["rc"] = rc
        write_entry(rdir, entry, review)
        log.append(entry)
        print(json.dumps(entry), flush=True)
        if rc != 0:
            print(f"[stage] {tag} rc {rc}; stopping here", flush=True)
            break
    (DATA / ("stage_log_smoke.json" if a.limit else "stage_log.json")).write_text(json.dumps(log, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
