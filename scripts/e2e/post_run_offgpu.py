#!/usr/bin/env python3
"""After the max run: everything that needs no GPU, run on the control box after the VM is gone.

    python scripts/e2e/post_run_offgpu.py OUT_DIR            # OUT_DIR = the driver's --out
    python scripts/e2e/post_run_offgpu.py OUT_DIR --plan-only  # print the steps, run nothing

Steps (none touch the H100; the Wonder chart read is a paid Muse API call, cents):
  1. Wonder reads each measured sweep chart and names the next cap (one per sweep folder).
  2. EU 2024/1364 export from each signed sweep receipt (hash + signature checked first).
  3. One full E2E flow per pass (python -m wattnot.flow --gpu-dir): the pass's GPU layers are
     read as MEASURED, the off-GPU layers run here, and each pass gets its own signed flow
     receipt in OUT_DIR/flow/<pass>. SAM/Wonderchart inside the flow stay MOCK unless
     --live-meta is passed (paid, cents; Akash's go).
  4. Replay pack: copy every raw reading and receipt into OUT_DIR/replay with a manifest
     (sha256 per file). The pack is labelled REPLAY: the demo shows the real MEASURED numbers
     as a replay of this run, never as live.
Nothing is deleted; existing files are never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ("*.jsonl", "*.json", "*.csv", "*.log", "*.txt")


def steps(out: Path) -> list[list[str]]:
    py = sys.executable
    cmds = []
    for sweep in sorted(p for g in ("t1-sweep*", "p*-sweep") for p in (out / "runs").glob(g)):
        cmds.append(["bash", "-c", f"{py} scripts/sweep_to_trials.py {sweep} && {py} -m "
                     f"wattnot.wonderchart.run --live --sweep {sweep}/sweep_trials.json "
                     f"--out {sweep}/wonder"])
        rc = [str(p) for p in sorted(sweep.glob("proof-*.json"))
              if not p.name.endswith(".sigstore.json")]
        if rc:
            cmds.append([py, "-m", "wattnot.cli", "report", "eu", "--receipts", *rc,
                         "--out", str(sweep / "eu" / "eu-2024-1364")])
    return cmds


def pass_dirs(out: Path) -> list[tuple[str, str, Path]]:
    """Link each pass's results into one folder the flow reads: sweep from the control box,
    the rest from the pulled host results. Links only; nothing is copied or overwritten."""
    sys.path.insert(0, str(ROOT / "src"))
    from wattnot.longrun.driver import MAX_PASS_LAYERS_MIN, MAX_PASSES
    res = []
    for pas, tag, _grid, ds in MAX_PASSES:
        d = out / "passes" / pas
        d.mkdir(parents=True, exist_ok=True)
        src = {"sweep": out / "runs" / tag}
        for layer in MAX_PASS_LAYERS_MIN:
            if layer != "sweep":
                src[layer] = out / "pulled" / "run2" / "results" / f"{pas}-{layer}"
        for layer, s in src.items():
            if s.exists() and not (d / layer).exists():
                (d / layer).symlink_to(s.resolve())
        res.append((pas, ds, d))
    return res


def replay_pack(out: Path) -> Path:
    dest = out / "replay"
    dest.mkdir(exist_ok=True)
    files = []
    for pat in RAW:
        for p in sorted(out.rglob(pat)):
            if dest in p.parents or p.name.endswith(".pem"):
                continue
            rel = p.relative_to(out)
            tgt = dest / rel
            if not tgt.exists():
                tgt.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, tgt)
            files.append({"path": str(rel), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    man = dest / "manifest.json"
    if not man.exists():
        man.write_text(json.dumps({"label": "REPLAY", "of": "MEASURED H100 max run",
                                   "note": "recorded readings, replayed; not a live run",
                                   "files": files}, indent=1))
    return man


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--plan-only", action="store_true")
    a = ap.parse_args(argv)
    bad = 0
    cmds = steps(a.out)
    for pas, ds, d in pass_dirs(a.out):
        cmds.append([sys.executable, "-m", "wattnot.flow", "--gpu-dir", str(d), "--dataset", ds,
                     "--tag", pas, "--out", str(a.out / "flow" / pas)])
    for c in cmds:
        print("$", " ".join(c))
        if not a.plan_only:
            bad += subprocess.run(c, cwd=ROOT).returncode != 0
    if not a.plan_only:
        print("replay manifest:", replay_pack(a.out))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
