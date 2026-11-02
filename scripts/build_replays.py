#!/usr/bin/env python3
"""Stitch the four demo replays (Modal round 1 and 2, Baseten round 1 and 2) from the
final good run data on the data branches. Free: reads git objects only, no live calls.

Each replay folder gets:
  events.jsonl            stitched events; every line keeps its own label and gains
                          "replay": {"label": "REPLAY", "source": <source id>}
  receipt.json (+ .sigstore.json)   the round's signed receipt, copied byte for byte
  eu_report.html          Modal: wattnot.report.eu.generate_report on the signed NVML energy
                          receipt (energy-receipt.json); Baseten: the round's own EU page
  round_eu_report.html    the EU page the round wrote next to its receipt
  blocks/<source>/...     copied block results (attachments keep their own signatures)
  manifest.json           every file: source ref, commit, path, sha256

Usage: python scripts/build_replays.py [--only modal-r1] [--out replays]
Load one: python -m wattnot.dash --replay-only --events replays/<name>/events.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MF = "data/modal-full-1101"


def _src(ref, path):
    return {"ref": ref, "path": path}


# Which events to drop from the base round, keyed by (node, sub), when a later good run of
# that block replaces it. Post-round eye/hearing checks in r05 were MOCK copies (see
# fix/eye-hearing-energy), so they go too when the block-only eye+hearing run replaces them.
ROUNDS: dict[str, dict] = {
    "modal-r1": {
        "title": "Modal round 1 (A100, public gsm8k+humaneval): r04f, LangChain from the r04f rerun",
        "provider": "modal",
        "base": {"id": "r04f", "ref": "data/modal-full-1101",
                 "events": [f"{MF}/r04f.without.events.jsonl", f"{MF}/r04f.with.events.jsonl"],
                 "receipt": f"{MF}/r04f/features/receipt.json",
                 "energy_receipt": f"{MF}/r04f/sweep/rcpt_20261101_modal_full_r04f.json"},
        "blocks": [
            {"id": "r04f-langchain-20261102T112336Z", "block": "langchain",
             "ref": "data/modal-full-1101",
             "dir": f"{MF}/attachments/r04f/r04f-langchain-20261102T112336Z",
             "replaces": [["effort_router", "langchain"]],
             "why": "rerun after fc-tune (final-check tuning, main 95b4431); r04f's own LangChain "
                    "pass ran before that fix"},
        ],
        "gaps": [
            "Round verdict is REJECTED on p99 only (88.4 -> 104.6 s, MEASURED). The fix "
            "(step-up latency guard, main 0da21ff) was never rerun on a full Modal round 1; "
            "round 2 (r05) is the post-fix Modal round. Shown as recorded, not hidden.",
            "Eye and hearing ran only on the WattNot server in r04f (no stock-server arm).",
        ],
    },
    "modal-r2": {
        "title": "Modal round 2 (A100, public gsm8k+humaneval): r05, LangChain fix rerun, "
                 "eye+hearing both-arms rerun, attribution arms",
        "provider": "modal",
        "base": {"id": "r05", "ref": "data/modal-full-1101",
                 "events": [f"{MF}/r05.without.events.jsonl", f"{MF}/r05.with.events.jsonl"],
                 "receipt": f"{MF}/r05/features/receipt.json",
                 "energy_receipt": f"{MF}/r05/sweep/rcpt_20261101_modal_full_r05.json"},
        "blocks": [
            {"id": "r05-langchain-20261102T140740Z", "block": "langchain",
             "ref": "data/modal-r05-langchain-fix",
             "dir": f"{MF}/attachments/r05/r05-langchain-20261102T140740Z",
             "replaces": [["effort_router", "langchain"]],
             "why": "rerun on the short-conversation bypass fix (db7f925); quality held, "
                    "saver_on 20/20 vs saver_off 19/20 MEASURED"},
            {"id": "r05-eye-hearing-20261102T153519Z", "block": "eye+hearing",
             "ref": "data/modal-r05-eye-hearing",
             "dir": f"{MF}/attachments/r05/r05-eye-hearing-20261102T153519Z",
             "replaces": [["gates", "eye"], ["gates", "spoken"], ["gates", "hearing"]],
             "why": "both arms (stock + WattNot) with NVML energy (fix/eye-hearing-block-arms); "
                    "r05's post-round eye/hearing checks were MOCK copies"},
            {"id": "r05-ablate-20261102T145207Z", "block": "attribution",
             "ref": "data/modal-r05-attribution",
             "dir": f"{MF}/attachments/r05/r05-ablate-20261102T145207Z",
             "replaces": [],
             "why": "extra block: arms A/B/C split of the r05 saving (batching vs levers vs cache)"},
        ],
        "gaps": [],
    },
    "baseten-r1": {
        "title": "Baseten round 1 (Muse Spark open, public gsm8k+humaneval): r02 full",
        "provider": "baseten",
        "base": {"id": "r02", "ref": "data/baseten-1102-full",
                 "events": ["data/baseten-1102-full/r02/events-without_wattnot.jsonl",
                            "data/baseten-1102-full/r02/events-with_wattnot.jsonl"],
                 "receipt": "data/baseten-1102-full/r02/features/receipt.json"},
        "blocks": [
            {"id": "r02-langchain", "block": "langchain", "ref": "data/baseten-1102-langchain",
             "dir": "data/baseten-1102-langchain/r02-langchain", "replaces": [],
             "summary_from": "langchain_pass.json",
             "why": "LangChain pass on Baseten (own 0..20), run separately from the r02 round"},
        ],
        "gaps": ["The LangChain block wrote no events file; the replay shows one summary event "
                 "built from its langchain_pass.json (numbers and label copied as recorded)."],
    },
    "baseten-r2": {
        "title": "Baseten round 2 (Muse Spark open, own set): r03 rerun on the step-up budget fix",
        "provider": "baseten",
        "base": {"id": "r03-rerun", "ref": "data/baseten-1102-own-rerun",
                 "events": ["data/baseten-1102-own-rerun/events/events-without_wattnot.jsonl",
                            "data/baseten-1102-own-rerun/events/events-with_wattnot.jsonl"],
                 "receipt": "data/baseten-1102-own-rerun/r03/r03/features/receipt.json"},
        "blocks": [
            {"id": "r03-langchain", "block": "langchain", "ref": "data/baseten-1102-own-e2e",
             "dir": "data/baseten-1102-own/langchain", "replaces": [],
             "summary_from": "langchain_pass.json",
             "why": "the rerun repeated only the main round; LangChain own 20/20 both arms "
                    "comes from r03 (same day, same set)"},
        ],
        "gaps": ["LangChain is from r03, not the rerun: it ran before the step-up budget fix "
                 "(a408281). The fix changes main-round step-ups only.",
                 "The LangChain block wrote no events file; one summary event is built from "
                 "its langchain_pass.json."],
    },
}


def git(*a: str) -> bytes:
    return subprocess.run(["git", *a], cwd=ROOT, check=True, capture_output=True).stdout


def resolve(ref: str) -> str:
    for r in (f"origin/{ref}", ref):
        try:
            return git("rev-parse", r).decode().strip()
        except subprocess.CalledProcessError:
            continue
    raise SystemExit(f"ref not found: {ref}")


def show(sha: str, path: str) -> bytes:
    return git("show", f"{sha}:{path}")


def ls(sha: str, d: str) -> list[str]:
    return [p for p in git("ls-tree", "-r", "--name-only", sha, d).decode().splitlines() if p]


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def tag(lines: bytes, source: str) -> list[dict]:
    out = []
    for raw in lines.decode("utf-8").splitlines():
        if raw.startswith("{"):
            e = json.loads(raw)
            e["replay"] = {"label": "REPLAY", "source": source}
            out.append(e)
    return out


def node_sub(e: dict) -> tuple:
    d = e.get("data") or {}
    return (d.get("node"), d.get("sub"))


def build(name: str, spec: dict, out_root: Path) -> dict:
    from wattnot.report.eu import check_receipt_file, generate_report
    out = out_root / name
    out.mkdir(parents=True, exist_ok=True)
    files: list[dict] = []

    def put(rel: str, data: bytes, src: dict | None):
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        files.append({"file": rel, "sha256": sha256(data), "bytes": len(data), "source": src})

    base = spec["base"]
    bsha = resolve(base["ref"])
    drop = {tuple(x) for b in spec["blocks"] for x in b["replaces"]}
    events: list[dict] = []
    for p in base["events"]:
        events += [e for e in tag(show(bsha, p), base["id"]) if node_sub(e) not in drop]
    for b in spec["blocks"]:
        s = resolve(b["ref"])
        for p in ls(s, b["dir"]):
            rel = p[len(b["dir"]) + 1:]
            if rel.endswith((".mp4", ".wav", ".png")):
                continue
            put(f"blocks/{b['id']}/{rel}", show(s, p), {"ref": b["ref"], "commit": s, "path": p})
        ev = f"{b['dir']}/events.jsonl"
        if ev in ls(s, b["dir"]):
            events += [e for e in tag(show(s, ev), b["id"]) if e.get("type") != "stage.features"]
        elif b.get("summary_from"):
            doc = json.loads(show(s, f"{b['dir']}/{b['summary_from']}"))
            keep = {k: v for k, v in doc.items() if not isinstance(v, (list, dict))}
            for k in ("arms", "summary", "results"):
                if isinstance(doc.get(k), dict):
                    keep[k] = doc[k]
            events.append({"type": "step.finished", "ts": None, "run": f"{spec['provider']} {b['id']}",
                           "step_id": f"replay-{b['id']}",
                           "data": {"node": "effort_router", "sub": b["block"],
                                    "label": doc.get("label", "ESTIMATE"),
                                    "summary": f"{b['block']}: summary from {b['summary_from']}",
                                    "result": keep},
                           "replay": {"label": "REPLAY", "source": b["id"],
                                      "built_from": f"{b['dir']}/{b['summary_from']}"}})
    events.sort(key=lambda e: (e.get("ts") is None, e.get("ts") or ""))
    put("events.jsonl", "".join(json.dumps(e) + "\n" for e in events).encode(), None)

    rc = show(bsha, base["receipt"])
    bundle_rel = base["receipt"][:-5] + ".sigstore.json"
    put("receipt.json", rc, {"ref": base["ref"], "commit": bsha, "path": base["receipt"]})
    put("receipt.sigstore.json", show(bsha, bundle_rel),
        {"ref": base["ref"], "commit": bsha, "path": bundle_rel})
    _, signed = check_receipt_file(out / "receipt.json")
    if signed is not True:
        raise SystemExit(f"{name}: receipt signature did not verify ({signed})")
    # EU 2024/1364 fields come from the GPU energy receipt (NVML sweep, signed) through the
    # repo's generator. A hosted Baseten round has no GPU receipt: its EU page is the one the
    # round's own eu step wrote from the signed round receipt, copied unchanged.
    eu_rel = base["receipt"].rsplit("/", 1)[0] + "/eu_report.html"
    put("round_eu_report.html", show(bsha, eu_rel), {"ref": base["ref"], "commit": bsha, "path": eu_rel})
    if base.get("energy_receipt"):
        er = base["energy_receipt"]
        put("energy-receipt.json", show(bsha, er), {"ref": base["ref"], "commit": bsha, "path": er})
        erb = er[:-5] + ".sigstore.json"
        put("energy-receipt.sigstore.json", show(bsha, erb), {"ref": base["ref"], "commit": bsha, "path": erb})
        if check_receipt_file(out / "energy-receipt.json")[1] is not True:
            raise SystemExit(f"{name}: energy receipt signature did not verify")
        put("eu_report.html", generate_report(out / "energy-receipt.json", format="html").encode(),
            {"generated_by": "wattnot.report.eu.generate_report", "from": "energy-receipt.json"})
    else:
        put("eu_report.html", show(bsha, eu_rel), {"ref": base["ref"], "commit": bsha, "path": eu_rel})
    manifest = {
        "spec": "wattnot.replay-manifest/1", "name": name, "title": spec["title"],
        "label": "REPLAY", "provider": spec["provider"], "live_calls": False,
        "base": {**base, "commit": bsha},
        "blocks": [{k: v for k, v in b.items()} | {"commit": resolve(b["ref"])}
                   for b in spec["blocks"]],
        "gaps": spec["gaps"], "receipt_signed": True, "events": len(events),
        "files": files,
        "launch": f"python -m wattnot.dash --replay-only --events replays/{name}/events.jsonl",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append")
    ap.add_argument("--out", default=str(ROOT / "replays"))
    a = ap.parse_args()
    sys.path.insert(0, str(ROOT / "src"))
    built: list[dict] = []
    for name, spec in ROUNDS.items():
        if a.only and name not in a.only:
            continue
        m = build(name, spec, Path(a.out))
        print(f"{name}: {m['events']} events, {len(m['files'])} files, gaps {len(m['gaps'])}")
        built.append(m)
    if not a.only:
        write_index(Path(a.out), built)
    return 0


def write_index(out: Path, ms: list[dict]) -> None:
    L = ["# Demo replays", "",
         "Four end-to-end replays stitched from the final good run of every block. Replay only:",
         "no GPU, no Meta or Baseten call. Every event keeps its own label (MEASURED, ESTIMATE, MOCK)",
         "and carries `replay.label = REPLAY` plus the source run. Built by `scripts/build_replays.py`;",
         "superseded and failed runs are listed in `data/archive/INDEX.md`.", "",
         "| Replay | What | Base run (commit) | Replaced blocks | Gaps | Launch |", "|---|---|---|---|---|---|"]
    for m in ms:
        b = m["base"]
        blocks = "<br>".join(f"{x['block']}: {x['id']} ({x['ref']} {x['commit'][:7]})" for x in m["blocks"])
        gaps = "<br>".join(m["gaps"]) or "none"
        L.append(f"| [{m['name']}]({m['name']}/manifest.json) | {m['title']} | {b['id']} on "
                 f"{b['ref']} ({b['commit'][:7]}) | {blocks} | {gaps} | `{m['launch']}` |")
    L += ["", "Check one: `python -m pytest tests/test_replays.py`.",
          "Receipts: `receipt.json` is the round's signed receipt (offline Ed25519, no Rekor), copied",
          "byte for byte. Modal replays also carry `energy-receipt.json` (signed NVML sweep receipt);",
          "their `eu_report.html` is generated from it by `wattnot.report.eu`. Baseten is a hosted API",
          "with no GPU meter, so its `eu_report.html` is the page the round wrote from its receipt.", ""]
    (out / "INDEX.md").write_text("\n".join(L))


if __name__ == "__main__":
    raise SystemExit(main())
