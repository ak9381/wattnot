"""Copy the 3 Nov H100 long run (pass 1 + pass 2) into replays/h100-max-1103 for judges.

Usage: python scripts/build_judge_bundle.py SRC_DIR [--holdout HOLDOUT_DIR]

SRC_DIR is the run's live-max-1103 folder (live.jsonl + recordings/<block>/).
Copies events and each block's meta/state/receipt as recorded (MEASURED), drops
absolute local paths, and writes a manifest with sha256 of every file. The sealed
holdout is added the same way once it lands (--holdout).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "replays" / "h100-max-1103"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def copy_blocks(src: Path, dst: Path) -> list[str]:
    names = []
    for b in sorted((src / "recordings").iterdir()):
        if not (b / "receipt.json").is_file():
            continue
        d = dst / "blocks" / b.name
        d.mkdir(parents=True, exist_ok=True)
        for f in ("receipt.json", "state.json", "timeline.jsonl"):
            if (b / f).is_file():
                shutil.copyfile(b / f, d / f)
        meta = json.loads((b / "meta.json").read_text())
        meta["replay"] = f"replays/h100-max-1103/blocks/{b.name}/timeline.jsonl"
        (d / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
        names.append(b.name)
    return names


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("--holdout", type=Path)
    a = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(a.src / "live.jsonl", OUT / "events.jsonl")
    blocks = copy_blocks(a.src, OUT)
    for pr in sorted((a.src / "runs").glob("*/proof-*.json")):  # signed sweep receipts
        d = OUT / "proofs" / pr.parent.name
        d.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pr, d / pr.name)
    if a.holdout:
        blocks += copy_blocks(a.holdout, OUT)
    files = {str(p.relative_to(OUT)): sha(p) for p in sorted(OUT.rglob("*"))
             if p.is_file() and p.name != "manifest.json"}
    man = {"replay": "h100-max-1103", "label": "MEASURED (replay of a recorded run)",
           "gpu": "H100 80GB, E2E Networks", "model": "meta-models/Muse-Glimmer-30B",
           "note": "meta.json 'replay' paths are rewritten to repo paths; nothing else is changed. "
                   "Block receipt hashes cover the original meta.json, so check them against "
                   "state.json and timeline.jsonl only. Events keep the recording machine's "
                   "receipt paths as recorded; the same receipts are in proofs/<block>/.",
           "blocks": blocks, "files": files}
    (OUT / "manifest.json").write_text(json.dumps(man, indent=1) + "\n")
    print(f"{OUT}: {len(blocks)} blocks, {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
