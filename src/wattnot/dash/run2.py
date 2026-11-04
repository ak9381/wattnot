"""The headline RUN2 numbers for the v2 dashboard's before/after block.

Source file (read first when present):
    data/h100-run2/vm/steps/cap350.json  (Llama 3.1 8B, 350 W cap vs 700 W stock)

That file holds per-pair repeats. The loader below averages the repeats the
same way the file's own ``mean`` fields do. When the file is missing (for
example in a packaged copy without the raw data), the ``FALLBACK`` constant
below carries the same means, rounded for display:

* energy per request: -27.1% (MEASURED)
* p99 latency: +7.7% (MEASURED)
* answer quality: 93.7% stock vs 93.7% capped, unchanged (MEASURED)

Cost and water are NOT in the file. They are ESTIMATEs the dashboard derives
from measured joules with the bundle's price and water factors.
"""

from __future__ import annotations

import json
from pathlib import Path

SOURCE = "data/h100-run2/vm/steps/cap350.json"

FALLBACK = {
    "model": "Llama 3.1 8B Instruct",
    "cap_w": 350,
    "stock_w": 700,
    "source": SOURCE,
    "from_file": False,
    "j_per_req_stock": 15.959,
    "j_per_req_cap": 11.635,
    "saved_pct": -27.1,
    "p99_s_stock": 3.605,
    "p99_s_cap": 3.882,
    "p99_change_pct": 7.7,
    "quality_stock_pct": 93.7,
    "quality_cap_pct": 93.7,
    "label": "MEASURED",
}


# RUN2 served Llama 3.1 8B, which does not count as a Meta model for this hackathon.
# It is hidden from every dashboard surface; the raw data under data/h100-run2 is kept.
SHOW_RUN2 = False
RUN2_ID = "run2-llama-350w"
RUN2_NAME = "H100 RUN2 · 350 W"
RUN2_DATE = "2026-10-27"

#: A file in data/cloud counts as a run fixture only if it declares this format, so a stray
#: JSON (a meter sample, notes) is not mistaken for a run.
FIXTURE_FORMAT = "wattnot.cloud-fixture/1"


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def load(root: Path | str | None = None) -> dict:
    """Headline RUN2 numbers, hidden from the dashboard (empty dict) unless SHOW_RUN2.
    RUN2 served a non-Meta model; its data stays in the repo as evidence."""
    if not SHOW_RUN2:
        return {"hidden": True}
    return _load(root)


def _load(root: Path | str | None = None) -> dict:
    if root is not None:
        p = Path(root) / SOURCE
        if p.is_file():
            try:
                d = json.loads(p.read_text())
            except ValueError:
                return dict(FALLBACK)
            stock = _mean(d.get("j_per_req_stock") or [])
            cap = _mean(d.get("j_per_req_cap") or [])
            p99 = d.get("p99_s") or {}
            q = d.get("quality") or {}
            if stock and cap:
                return {
                    "model": "Llama 3.1 8B Instruct",
                    "cap_w": d.get("cap_w", 350),
                    "stock_w": 700,
                    "source": SOURCE,
                    "from_file": True,
                    "j_per_req_stock": round(stock, 3),
                    "j_per_req_cap": round(cap, 3),
                    "saved_pct": round((cap / stock - 1) * 100, 1),
                    "p99_s_stock": round(float(p99["stock"]), 3) if "stock" in p99 else None,
                    "p99_s_cap": round(float(p99["cap"]), 3) if "cap" in p99 else None,
                    "p99_change_pct": round(d["p99_change_pct"]["mean"], 1),
                    "quality_stock_pct": round(float(q["stock_pct"]), 1) if "stock_pct" in q else None,
                    "quality_cap_pct": round(float(q["cap_pct"]), 1) if "cap_pct" in q else None,
                    "label": "MEASURED",
                }
    return dict(FALLBACK)


def _block_event(b: dict, i: int) -> dict:
    """One measured repeat as a phase event. Every number is MEASURED."""
    return {"repeat": i, "cap_w": b.get("cap_w"), "label": "MEASURED",
            "j_per_req": b.get("j_per_req"), "p99_s": b.get("p99_s"),
            "pass_pct": b.get("pass_pct"), "requests": b.get("requests"),
            "raw": b.get("raw"), "raw_sha256": b.get("raw_sha256")}


def _phase_metrics(blocks: list[dict], quality_pct: float | None) -> dict:
    req = sum(b.get("requests") or 0 for b in blocks)
    return {"label": "MEASURED",
            "j_per_req": _mean([b.get("j_per_req") for b in blocks]),
            "p99_s": _mean([b.get("p99_s") for b in blocks]),
            "mean_w": _mean([b.get("sampled_mean_w") for b in blocks]),
            "quality_pct": quality_pct, "requests": req, "repeats": len(blocks)}


def build_record(root: Path | str | None = None) -> dict:
    """One run record holding both phases.

    Phase 1 (``baseline``) is the stock 700 W arm, Phase 2 (``wattnot``) the
    350 W capped arm, both read from ``data/h100-run2/vm/steps/cap350.json``.
    Phase numbers are MEASURED; the record as served is a REPLAY of them.
    """
    head = _load(root)
    step = None
    if root is not None:
        p = Path(root) / SOURCE
        if p.is_file():
            try:
                step = json.loads(p.read_text())
            except ValueError:
                step = None
    blocks = (step or {}).get("blocks") or {}
    stock, capped = blocks.get("stock") or [], blocks.get("cap") or []
    q = (step or {}).get("quality") or {}
    return {
        "id": RUN2_ID, "run_name": RUN2_NAME, "model": head["model"],
        "date": RUN2_DATE, "label": "REPLAY",
        "source": [SOURCE, "data/cloud/recorded-run1.json",
                   "data/cloud/recorded-run2.json"],
        "phases": {
            "baseline": {
                "title": "Without WattNot", "label": "MEASURED",
                "cap_w": head["stock_w"],
                "metrics": _phase_metrics(stock, q.get("stock_pct")),
                "events": [_block_event(b, i) for i, b in enumerate(stock)],
            },
            "wattnot": {
                "title": "With WattNot", "label": "MEASURED",
                "cap_w": head["cap_w"],
                "metrics": {**_phase_metrics(capped, q.get("cap_pct")),
                            "saved_pct": head["saved_pct"],
                            "p99_change_pct": head["p99_change_pct"]},
                "events": [_block_event(b, i) for i, b in enumerate(capped)],
            },
        },
    }


def _fixture_record(root: Path, stem: str) -> dict | None:
    """A one-phase record from a saved API-reply fixture (baseline replies only)."""
    p = root / "data" / "cloud" / f"{stem}.json"
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text())
    except ValueError:
        return None
    replies = d.get("replies") or {}
    evs = [{"reply": k, "label": "REPLAY",
            "latency_s": (v or {}).get("latency_s"),
            "status": (v or {}).get("status")} for k, v in sorted(replies.items())]
    return {
        "id": f"cloud-{stem}", "run_name": f"Hosted fixture · {stem}",
        "model": d.get("model"), "date": (d.get("recorded_at") or "")[:10] or None,
        "label": "REPLAY", "source": [f"data/cloud/{stem}.json"],
        "phases": {
            "baseline": {
                "title": "Without WattNot", "label": "REPLAY",
                "metrics": {"label": "REPLAY", "replies": len(replies),
                            "j_per_req": None, "p99_s": None,
                            "quality_pct": None, "requests": len(replies),
                            "repeats": 1},
                "events": evs,
            },
            "wattnot": {
                "title": "With WattNot", "label": None,
                "metrics": None, "events": [],
                "note": "No capped arm was recorded for this fixture; only the baseline replies are stored.",
            },
        },
    }


def discover_fixtures(root: Path) -> list[str]:
    """Stems of every cloud run fixture under data/cloud, newest first.

    Discovered from disk, not a hard-coded list, so a run saved later (e.g. an Oct 31 run) shows
    up without editing this file. A file is a run fixture only if it declares ``FIXTURE_FORMAT``.
    """
    found: list[tuple[str, str]] = []  # (recorded_at, stem)
    for p in sorted((Path(root) / "data" / "cloud").glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("format") == FIXTURE_FORMAT:
            found.append((str(d.get("recorded_at") or ""), p.stem))
    found.sort(key=lambda t: (t[0], t[1]), reverse=True)  # newest recorded run first
    return [stem for _, stem in found]


def _matrix_record(root: Path, rid: str) -> dict | None:
    """A two-phase record from a real Run matrix round (out/matrix/<rid>.json), e.g. a Baseten
    round: accuracy, tokens, USD and latency MEASURED; energy ESTIMATE (hosted API, no meter)."""
    p = Path(root) / "out" / "matrix" / f"{rid}.json"
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text())
    except ValueError:
        return None
    if d.get("label") != "MEASURED" or d.get("smoke") or d.get("status") != "done":
        return None
    arms = (d.get("results") or {}).get("per_arm") or {}

    def phase(arm: str, title: str) -> dict:
        a = arms.get(arm) or {}
        n = max(1, a.get("answers") or 1)
        wh = (a.get("energy_wh") or {}).get("value")
        return {"title": title, "label": "MEASURED", "cap_w": None,
                "metrics": {"label": "MEASURED", "energy_label": "ESTIMATE",
                            "j_per_req": round(wh * 3600 / n, 3) if wh is not None else None,
                            "j_per_correct": (a.get("j_per_correct") or {}).get("value"),
                            "p99_s": (a.get("item_latency_s") or {}).get("p99"),
                            "p50_s": (a.get("item_latency_s") or {}).get("p50"),
                            "quality_pct": round(100 * a["accuracy"], 2) if a.get("accuracy") is not None else None,
                            "usd": (a.get("usd") or {}).get("value"), "tokens": a.get("tokens"),
                            "requests": a.get("answers"), "per_set": a.get("per_set"), "repeats": 1},
                "events": []}

    b, w = phase("without_wattnot", "Without WattNot"), phase("with_wattnot", "With WattNot")
    bj, wj = b["metrics"]["j_per_req"], w["metrics"]["j_per_req"]
    bp, wp = b["metrics"]["p99_s"], w["metrics"]["p99_s"]
    w["metrics"]["saved_pct"] = round(100 * (wj - bj) / bj, 2) if bj and wj is not None else None
    w["metrics"]["p99_change_pct"] = round(100 * (wp - bp) / bp, 2) if bp and wp is not None else None
    leg = d.get("leg", "")
    return {"id": f"matrix-{rid}", "kind": "matrix", "leg": leg,
            "run_name": f"Matrix {rid} · {leg.capitalize()} · Muse Spark Open",
            "model": d.get("model"), "date": (d.get("started_utc") or "")[:10] or None,
            "label": "MEASURED", "source": [f"out/matrix/{rid}.json", d.get("raw_dir", "")],
            "matrix": {"spark": d.get("spark_summary"), "spend": d.get("spend"), "levers": d.get("levers"),
                       "note": d.get("note")},
            "phases": {"baseline": b, "wattnot": w}}


def matrix_ids(root: Path) -> list[str]:
    return [p.stem for p in sorted((Path(root) / "out" / "matrix").glob("r*.json"))]


def list_runs(root: Path | str | None = None) -> list[dict]:
    """Run summaries by run name: id, name, model, date. RUN2 first, then cloud runs."""
    root = Path(root) if root is not None else Path(__file__).resolve().parents[3]
    out = []
    if SHOW_RUN2:
        rec = build_record(root)
        out.append({"id": rec["id"], "name": rec["run_name"], "model": rec["model"], "date": rec["date"]})
    for rid in matrix_ids(root):
        m = _matrix_record(root, rid)
        if m is not None:
            out.append({"id": m["id"], "name": m["run_name"], "model": m["model"], "date": m["date"]})
    for stem in discover_fixtures(root):
        f = _fixture_record(root, stem)
        if f is not None:
            out.append({"id": f["id"], "name": f["run_name"],
                        "model": f["model"], "date": f["date"]})
    return out


def hosted_ab(root: Path) -> dict | None:
    """The newest live hosted A/B report (reports/hosted-ab/live-*.json), or None."""
    files = sorted((Path(root) / "reports" / "hosted-ab").glob("live-*.json"))
    if not files:
        return None
    try:
        d = json.loads(files[-1].read_text())
    except (OSError, ValueError):
        return None
    d.setdefault("mode", "LIVE")
    d["source"] = f"reports/hosted-ab/{files[-1].name}"
    return d


def get_run(root: Path | str | None, run_id: str) -> dict | None:
    """The full two-phase record for one run, or None. The live hosted A/B
    (Baseten/Modal) is attached as rec["hosted_ab"] when the report exists."""
    root = Path(root) if root is not None else Path(__file__).resolve().parents[3]
    if run_id == RUN2_ID:
        if not SHOW_RUN2:
            return None
        rec = build_record(root)
    elif run_id.startswith("matrix-"):
        rec = _matrix_record(root, run_id[len("matrix-"):])
    elif run_id.startswith("cloud-"):
        rec = _fixture_record(root, run_id[len("cloud-"):])
    else:
        return None
    if rec is not None and "hosted_ab" not in rec:
        h = hosted_ab(root)
        if h is not None:
            rec["hosted_ab"] = h
    return rec
