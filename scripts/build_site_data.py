#!/usr/bin/env python3
# ruff: noqa: E501
"""Rebuild web/site/data/ from the backend, so the site only ever shows real output.

Runs the WattNot CLI offline (replayed fixtures, the public TEST key) and copies the results.
Nothing here needs a GPU, a network or a secret. Run it after the backend changes:

    python scripts/build_site_data.py

Everything the site shows is labelled: the cloud receipt, flex run, grid figures and
certificates in this set are SIMULATED or MOCK. The receipts under `receipts/` are the
bundled conformance vectors (ESTIMATE, NOISE, SIMULATED and deliberately forged ones).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "site" / "data"
VEC = ROOT / "verifier" / "vectors"
KEY = ROOT / "verifier" / "samples" / "test-key.pem"
STAMP = "2026-10-27T10:00:00+00:00"

# (file stem, short title shown in the picker, one line of context)
SAMPLES = [
    ("real-estimate", "Hosted run, ESTIMATE", "A saving that holds up. Signed offline, so it tops out at ESTIMATE."),
    ("real-measured", "GPU run, MEASURED", "Measured on a GPU and signed offline: ESTIMATE, not VALID (no public log entry)."),
    ("real-joules", "With a joules block", "Joules per answer are re-derived in your browser and must match."),
    ("real-cloud", "Hosted model, SIMULATED", "From the replayed demo fixture. Never above SIMULATED."),
    ("real-noise", "Saving is noise", "The change is inside run-to-run noise, so nothing is billed: NOISE."),
    ("forged-edited", "Edited after signing", "Someone changed the billed saving. The hash no longer matches."),
    ("forged-rehashed", "Edited and re-hashed", "New hash, old signature. The signature fails."),
    ("forged-otherkey", "Re-signed by another key", "Valid maths, but not a key you trust."),
    ("forged-joules", "Joules block edited", "Hash and signature re-made, but the numbers disagree."),
]


def wattnot(*args: str, ok: tuple[int, ...] = (0,)) -> str:
    p = subprocess.run([sys.executable, "-m", "wattnot.cli", *args], cwd=ROOT,
                       capture_output=True, text=True)
    if p.returncode not in ok:
        raise SystemExit(f"wattnot {' '.join(args)} -> {p.returncode}\n{p.stderr}")
    return p.stdout


def dump(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def receipts() -> None:
    idx = []
    for stem, title, note in SAMPLES:
        (OUT / "receipts").mkdir(parents=True, exist_ok=True)
        shutil.copy(VEC / f"{stem}.json", OUT / "receipts" / f"{stem}.json")
        shutil.copy(VEC / f"{stem}.sigstore.json", OUT / "receipts" / f"{stem}.sigstore.json")
        idx.append({"id": stem, "title": title, "note": note})
    exp = json.loads((VEC / "expected.json").read_text())
    for row in idx:
        row["expected_grade"] = exp[row["id"]]["grade"]
    dump(OUT / "receipts" / "index.json", idx)
    shutil.copy(ROOT / "verifier" / "trusted-keys.json", OUT / "trusted-keys.json")


def flex(tmp: Path) -> None:
    out = tmp / "flex"
    wattnot("flex", "run", "--out", str(out), "--sign-key", str(KEY), "--created-at", STAMP,
            "--json")
    events = []
    (OUT / "flex").mkdir(parents=True, exist_ok=True)
    for rc in sorted(out.glob("rcpt_flex_*.json")):
        if rc.name.endswith((".sigstore.json", ".trace.json")):
            continue
        r = json.loads(rc.read_text())
        ev = r["flex"]["event"]["id"]
        # the chart only needs these keys; keep the file small
        # raw copies: the receipt holds the SHA-256 of the trace file's exact bytes
        shutil.copy(out / f"{rc.stem}.trace.json", OUT / "flex" / f"{ev}.trace.json")
        shutil.copy(rc, OUT / "flex" / f"{ev}.receipt.json")
        shutil.copy(out / f"{rc.stem}.sigstore.json", OUT / "flex" / f"{ev}.receipt.sigstore.json")
        events.append(ev)
    dump(OUT / "flex" / "index.json", {"events": sorted(events)})


def route(tmp: Path) -> None:
    demo = tmp / "cloud-demo"
    wattnot("cloud", "demo", "--out", str(demo))
    rc = next(p for p in sorted(demo.glob("cloud_*.json"))
              if not p.name.endswith(".audit.json"))
    replay = json.loads(wattnot("route", "replay", "--receipt", str(rc), "--json"))
    decisions = {}
    for c in replay["classes"]:
        decisions[c["class"]] = json.loads(
            wattnot("route", "decide", "--receipt", str(rc), "--class", c["class"], "--json"))
    dump(OUT / "route" / "replay.json", replay)
    # the demo run is stamped with the build time, so the page quotes this copy's hash
    shutil.copy(rc, OUT / "route" / "source-receipt.json")
    dump(OUT / "route" / "decisions.json", decisions)
    dump(OUT / "route" / "grid.json", json.loads(wattnot("route", "grid", "--json")))


def certificate(tmp: Path) -> None:
    certs = tmp / "certs"
    (OUT / "certificate").mkdir(parents=True, exist_ok=True)
    minted = []
    plan = [("real-estimate", False), ("real-cloud", True)]
    for stem, sim in plan:
        args = ["certificate", "mint", "--receipt", str(VEC / f"{stem}.json"), "--out", str(certs),
                "--issued-at", STAMP, "--json"]
        if sim:
            args.append("--allow-simulated")
        doc = json.loads(wattnot(*args))
        c = doc["certificate"]
        shutil.copy(certs / f"{c['serial']}.json", OUT / "certificate" / f"{c['serial']}.json")
        shutil.copy(certs / f"{c['serial']}.html", OUT / "certificate" / f"{c['serial']}.html")
        minted.append({"serial": c["serial"], "receipt": stem, "grade": c["receipt"]["grade"],
                       "basis": c["labels"]["basis"]})
    refused = []
    for stem, why, extra in (("real-noise", "A NOISE receipt", []),
                             ("real-estimate", "The same receipt again", []),
                             ("forged-edited", "A receipt that fails verification", []),
                             ("real-cloud", "A SIMULATED receipt without the demo flag", [])):
        p = subprocess.run([sys.executable, "-m", "wattnot.cli", "certificate", "mint",
                            "--receipt", str(VEC / f"{stem}.json"), "--out", str(certs),
                            "--issued-at", STAMP, "--json", *extra],
                           cwd=ROOT, capture_output=True, text=True)
        doc = json.loads(p.stdout)
        refused.append({"receipt": stem, "case": why, "minted": doc["minted"],
                        "reasons": doc.get("reasons", [])})
    reg = json.loads(wattnot("certificate", "register", "--register",
                             str(certs / "register.json"), "--json"))
    dump(OUT / "certificate" / "register.json", reg)
    dump(OUT / "certificate" / "index.json", {"minted": minted, "refused": refused})
    dump(OUT / "certificate" / "roadmap.json", json.loads(wattnot("certificate", "roadmap", "--json")))


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        receipts()
        flex(tmp)
        route(tmp)
        certificate(tmp)
    n = sum(1 for _ in OUT.rglob("*") if _.is_file())
    print(f"wrote {n} files under {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
