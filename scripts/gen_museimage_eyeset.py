#!/usr/bin/env python3
"""PAID one-time script: build the Muse Image eye set (gauges, racks, charts, documents).

Muse Image (muse-image-1.0) generates each scene with a known answer baked into the prompt.
An independent reader (Muse Wonder, Meta Model API) then has to read the answer back with the
same grader the eye gate uses; any image it misreads is dropped, so every label that ships is
verified readable. The kept set is written as a wattnot.eyetest/1 manifest that the existing
eye block reads with ``--assets data/vision/eyeset-museimage``.

    WATTNOT_GEN_VISION_GO=1 python scripts/gen_museimage_eyeset.py

Every image is charged on the Meta spend ledger before it is sent. Tests never run main().
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "data" / "vision" / "eyeset-museimage"
MODEL = "muse-image-1.0"
VERIFIER = "muse-wonder-1.0"
SOURCE = "Muse Image generated"

# (id, category, kind, question, answer, prompt). The answer must appear legibly in the scene.
SPECS: list[tuple[str, str, str, str, str, str]] = [
    # gauges / meters
    ("gauge-pdu-347", "gauge", "number", "What power in watts does the meter display show?", "347",
     "a close-up photo of a rack PDU digital power meter whose bright LED display reads '347 W'"),
    ("gauge-temp-68", "gauge", "number", "What temperature in degrees Celsius does the display show?", "68",
     "a close-up photo of a GPU temperature monitor panel whose large digital display reads '68 °C'"),
    ("gauge-flow-125", "gauge", "number", "What flow rate does the coolant meter display show?", "125",
     "a close-up photo of a liquid-cooling coolant flow meter with a digital display reading '125 L/min'"),
    ("gauge-volt-230", "gauge", "number", "What voltage does the panel meter show?", "230",
     "a close-up photo of a data-center electrical panel voltmeter whose digital readout shows '230 V'"),
    ("gauge-pue-14", "gauge", "number", "What PUE value does the dashboard screen show?", "1.4",
     "a wall-mounted data-center monitor showing one huge number 'PUE 1.4' on a dark screen"),
    ("gauge-hum-45", "gauge", "number", "What humidity percentage does the sensor display show?", "45",
     "a close-up photo of a wall humidity sensor whose LCD display reads '45 %'"),
    ("gauge-amp-16", "gauge", "number", "What current in amps does the breaker display show?", "16",
     "a close-up photo of a smart circuit breaker with a small digital display reading '16 A'"),
    ("gauge-fan-72", "gauge", "number", "What fan speed percentage does the display show?", "72",
     "a close-up photo of a server front-panel LCD reading 'FAN 72%' in large characters"),
    # rack photos
    ("rack-count-4", "rack", "count", "How many servers are mounted in the rack?", "4",
     "a front photo of an open server rack holding exactly four identical 2U servers, the rest of the rack empty, even lighting"),
    ("rack-count-6", "rack", "count", "How many servers are mounted in the rack?", "6",
     "a front photo of an open server rack holding exactly six identical 1U servers stacked together, the rest empty"),
    ("rack-count-3", "rack", "count", "How many servers are mounted in the rack?", "3",
     "a front photo of a server rack containing exactly three large GPU servers with gaps between them"),
    ("rack-label-b07", "rack", "text", "What is the rack label text?", "RACK-B07",
     "a photo of a server rack door with a large printed label reading 'RACK-B07'"),
    ("rack-label-c12", "rack", "text", "What is the rack label text?", "ROW-C12",
     "a photo of a data-center aisle sign hanging above racks reading 'ROW-C12' in big letters"),
    ("rack-led-red", "rack", "color", "What colour is the single status light that is lit on the server?", "red",
     "a close-up of a black server front panel with exactly one glowing red status LED and no other lights"),
    ("rack-cable-blue", "rack", "color", "What colour are the network cables?", "blue",
     "a close-up photo of a network switch with all patch cables coloured bright blue"),
    # charts
    ("chart-bar-tallest-green", "chart", "color", "What colour is the tallest bar in the chart?", "green",
     "a simple flat bar chart on white with four bars; the tallest bar is bright green, the others grey"),
    ("chart-bar-count-5", "chart", "count", "How many bars are in the chart?", "5",
     "a simple flat bar chart on white with exactly five blue bars of different heights, no labels"),
    ("chart-value-82", "chart", "number", "What value is printed above the highlighted bar?", "82",
     "a clean bar chart where one orange bar has the number '82' printed above it in bold, other bars grey and unlabelled"),
    ("chart-title-energy", "chart", "text", "What is the chart title?", "ENERGY",
     "a clean line chart on white with the single bold title word 'ENERGY' at the top"),
    ("chart-pie-largest-purple", "chart", "color", "What colour is the largest slice of the pie chart?", "purple",
     "a flat pie chart on white where the largest slice, more than half, is purple and the other slices are light grey"),
    ("chart-kpi-267", "chart", "number", "What percentage does the KPI card show?", "26.7",
     "a flat dashboard KPI card on white showing one large number '26.7%' and nothing else"),
    # documents
    ("doc-invoice-total-4520", "document", "number", "What is the invoice total in dollars?", "4520.00",
     "a printed invoice on a desk whose bold TOTAL line reads '$4,520.00', other rows faint"),
    ("doc-po-number", "document", "text", "What is the purchase order number?", "PO-5531",
     "a printed purchase order document with a large heading 'PO-5531' at the top"),
    ("doc-receipt-kwh-918", "document", "number", "How many kWh does the energy bill show?", "918",
     "a printed electricity bill whose highlighted usage box reads '918 kWh' in large type"),
    ("doc-ticket-id", "document", "text", "What is the ticket ID on the work order?", "WO-2207",
     "a printed maintenance work order with a large bold ticket ID 'WO-2207' at the top"),
    ("doc-stamp-approved", "document", "text", "What word is stamped on the document?", "APPROVED",
     "a paper form with a big red rubber stamp reading 'APPROVED' across it"),
    ("doc-sticky-colour", "document", "color", "What colour is the sticky note?", "yellow",
     "a single bright yellow sticky note on a white desk, top-down"),
    ("doc-checkboxes-3", "document", "count", "How many boxes are ticked on the form?", "3",
     "a printed checklist form with five checkboxes in a column, exactly three of them ticked with a black check mark"),
]


def _gen(base: str, key: str, prompt: str) -> bytes:
    body = json.dumps({"model": MODEL, "prompt": f"{prompt}. Burn a small 'SYNTHETIC' watermark into a corner.",
                       "n": 1, "size": "512x512", "response_format": "b64_json"}).encode()
    req = urllib.request.Request(f"{base.rstrip('/')}/images/generations", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:  # noqa: S310
        data = json.loads(r.read() or b"{}")
    return base64.b64decode(data["data"][0]["b64_json"])


def build_manifest(kept: list[dict], *, image_calls: int, verifier: str = VERIFIER) -> dict:
    return {
        "spec": "wattnot.eyetest/1",
        "label": "SYNTHETIC",
        "source": "GENERATED-META",
        "dataset_source": SOURCE,
        "note": "Eye set generated by Muse Image (muse-image-1.0): gauges, rack photos, charts and "
                "documents, each with a known answer. Every kept image was read back correctly by an "
                f"independent reader ({verifier}) with the eye-gate grader; misreads were dropped.",
        "generator": {"model": MODEL, "endpoint": "{META_BASE_URL}/images/generations",
                      "format": "b64 webp", "label": "SYNTHETIC", "image_calls": image_calls},
        "verifier": {"model": verifier, "rule": "wattnot.vision.gate.grade_answers must pass"},
        "energy_per_image_wh": {"value": 0.5, "label": "ESTIMATE",
                                "method": "assumed per-image figure (not metered)"},
        "items": kept,
    }


def main() -> int:
    if os.environ.get("WATTNOT_GEN_VISION_GO") != "1":
        print("refusing: PAID script. Set WATTNOT_GEN_VISION_GO=1.", file=sys.stderr)
        return 2
    from wattnot.meta.client import GuardedClient, load_key
    from wattnot.meta.ledger import SpendLedger
    from wattnot.vision.assets import EyeTestItem
    from wattnot.vision.client import ServedVisionAnswerer
    from wattnot.vision.gate import grade_answers

    key = load_key()
    if not key:
        print("no API key", file=sys.stderr)
        return 2
    base = os.environ.get("META_BASE_URL", "https://api.meta.ai/v1")
    ledger = SpendLedger()
    client = GuardedClient(key=key, ledger=ledger)
    reader = ServedVisionAnswerer(OUT, model=VERIFIER, client=lambda b: client(b, purpose="museimage-eyeset-verify"))
    OUT.mkdir(parents=True, exist_ok=True)
    kept, log, calls = [], [], 0
    for sid, cat, kind, q, ans, prompt in SPECS:
        f = OUT / f"{sid}.webp"
        if not f.exists():
            ledger.charge_image(MODEL, "museimage-eyeset-gen")
            calls += 1
            try:
                f.write_bytes(_gen(base, key, prompt))
            except Exception as e:  # noqa: BLE001
                log.append({"id": sid, "status": "gen-failed", "error": type(e).__name__})
                print(sid, "gen failed", type(e).__name__, flush=True)
                continue
        item = EyeTestItem(id=sid, file=f.name, kind=kind, question=q, answer=ans)
        body = reader._body(item)
        body["max_completion_tokens"] = 4000
        try:
            r = client(body, purpose="museimage-eyeset-verify")
            got = str(r["choices"][0]["message"].get("content") or "").strip()
        except Exception as e:  # noqa: BLE001
            got = ""
            print(sid, "verify error", type(e).__name__, flush=True)
        ok = grade_answers({sid: got}, [item])[0]["ok"]
        log.append({"id": sid, "category": cat, "expected": ans, "verifier_got": got[:64], "kept": ok})
        print(sid, "KEEP" if ok else "DROP", repr(got[:60]), flush=True)
        if ok:
            kept.append({"id": sid, "file": f.name, "kind": kind, "category": cat, "question": q,
                         "answer": ans, "watermark": "SYNTHETIC", "source": SOURCE})
        else:
            (OUT / "dropped").mkdir(exist_ok=True)
            f.rename(OUT / "dropped" / f.name)
        time.sleep(0.2)
    (OUT / "answers.json").write_text(json.dumps(build_manifest(kept, image_calls=calls), indent=2) + "\n")
    (OUT / "generation_log.json").write_text(json.dumps({
        "generated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "image_calls": calls,
        "generated_ok": sum(1 for x in log if x.get("status") != "gen-failed"),
        "kept": len(kept), "dropped": [x["id"] for x in log if not x.get("kept")], "rows": log}, indent=1) + "\n")
    print(f"kept {len(kept)}/{len(SPECS)}; image calls {calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
