"""The live plug feed: everything after Configure, built from gateway and node-agent records.

Sources (append-only JSONL):
* gateway savings log: one record per request (tokens MEASURED from provider usage, $ ESTIMATE,
  joules MEASURED on own GPU via NVML or ESTIMATE on an API)
* node log (optional): {"ts", "gpu", "power_w", "joules", "cap_w"} samples from NVML, MEASURED

When neither has a record, ``feed()`` returns ``{"source": "replay"}`` and the page falls back to
the recorded run, labelled REPLAY.
"""
from __future__ import annotations

import json
from pathlib import Path

from wattnot.gateway.savings import summarize

WUE_L_PER_KWH = 0.36   # ESTIMATE: on-site cooling (LBNL 2024), same as wattnot.impact


def _read(path: str | Path | None) -> list[dict]:
    if not path or not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def feed(savings_log: str | Path | None, node_log: str | Path | None = None, last: int = 50) -> dict:
    recs, node_all = _read(savings_log), _read(node_log)
    node = [n for n in node_all if n.get("kind", "sample") == "sample"]
    node_events = [n for n in node_all if n.get("kind") == "event"]
    if not recs and not node_all:
        return {"source": "replay", "label": "REPLAY",
                "note": "Nothing is plugged in yet. Showing the recorded run (REPLAY)."}
    series, ca, cb, ua, ub = [], 0.0, 0.0, 0.0, 0.0
    for r in recs:
        ca += r["actual"]["joules"]; cb += r["baseline"]["joules"]
        ua += r["actual"]["usd"]; ub += r["baseline"]["usd"]
        series.append({"ts": r["ts"], "receipt_id": r.get("receipt_id"),
                       "wh": round(ca / 3600, 5), "wh_baseline": round(cb / 3600, 5),
                       "usd": round(ua, 6), "usd_baseline": round(ub, 6),
                       "litres": round(ca / 3.6e6 * WUE_L_PER_KWH, 6),
                       "litres_baseline": round(cb / 3.6e6 * WUE_L_PER_KWH, 6),
                       "label": r["labels"]["joules"]})
    s = summarize(recs, last=last) if recs else None
    shadow = _read(Path(savings_log).with_name(Path(savings_log).stem + ".shadow.jsonl")) if savings_log else []
    if s and shadow:
        from wattnot.gateway.savings import _jpt
        jpt = recs[-1]["baseline"].get("j_per_token") or 7.86
        oh_wh = sum((p["base_in"] + p["base_out"]) * jpt for p in shadow) / 3600
        s["monitor"] = {"pairs": len(shadow), "agreement": round(sum(p["agree"] for p in shadow) / len(shadow), 3),
                        "overhead_wh": round(oh_wh, 4), "label": "ESTIMATE",
                        "net_saved_wh": round(s["energy_wh"]["baseline"] - s["energy_wh"]["actual"] - oh_wh, 4)}
        _ = _jpt
    power = [{"ts": n["ts"], "gpu": n.get("gpu", 0), "power_w": n.get("power_w"),
              "cap_w": n.get("cap_w"), "label": n.get("label", "MEASURED")} for n in node][-500:]
    nlabels = sorted({p["label"] for p in power})
    node_label = "MEASURED" if nlabels == ["MEASURED"] else "/".join(nlabels) or "REPLAY"
    node_j = sum(float(n.get("joules") or 0) for n in node)
    return {"source": "live", "label": s["energy_wh"]["label"] if s else "MEASURED",
            "summary": s, "series": series[-500:], "power": power,
            "node": {"samples": len(node), "joules": round(node_j, 1), "label": node_label,
                     "events": node_events[-50:]} if node_all else None,
            "water": {"litres": series[-1]["litres"] if series else 0,
                      "litres_baseline": series[-1]["litres_baseline"] if series else 0,
                      "label": f"ESTIMATE ({WUE_L_PER_KWH} L/kWh on-site cooling)"},
            "receipts": [{k: r.get(k) for k in ("receipt_id", "prev_receipt_id", "ts", "actual", "baseline",
                                                 "labels", "stepped_up", "guard_stopped", "status")}
                         for r in recs[-last:]][::-1]}


def receipt(savings_log: str | Path | None, rid: str) -> dict | None:
    return next((r for r in _read(savings_log) if r.get("receipt_id") == rid), None)


def eu_report(savings_log: str | Path | None, node_log: str | Path | None = None) -> dict:
    """Inference-level fields toward an EU 2024/1364 report, from live plug records only."""
    f = feed(savings_log, node_log)
    if f["source"] == "replay":
        return {"source": "replay", "label": "REPLAY", "note": f["note"]}
    s = f["summary"] or {}
    e = s.get("energy_wh", {})
    kwh = (f["node"]["joules"] / 3.6e6) if f.get("node") else (e.get("actual", 0) / 1000)
    return {"spec": "wattnot.eu-inference-fields/1", "source": "live",
            "basis": "gateway + node-agent records; not a facility meter",
            "requests": s.get("requests", 0),
            "energy_kwh": {"value": round(kwh, 6), "label": f["node"]["label"] if f.get("node") else e.get("label")},
            "energy_saved_kwh": {"value": round((e.get("baseline", 0) - e.get("actual", 0)) / 1000, 6),
                                 "label": "ESTIMATE (vs baseline)"},
            "water_litres": {"value": f["water"]["litres"], "label": f["water"]["label"]},
            "first_receipt": f["receipts"][-1]["receipt_id"] if f["receipts"] else None,
            "last_receipt": f["receipts"][0]["receipt_id"] if f["receipts"] else None}
