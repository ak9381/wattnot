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
    wo = _read(Path(savings_log).with_name(Path(savings_log).stem + ".without.jsonl")) if savings_log else []
    without = {"requests": len(wo), "wh": round(sum(x.get("joules", 0) for x in wo) / 3600, 4),
               "usd": round(sum(x.get("usd", 0) for x in wo), 6),
               "label": "ESTIMATE · Simulated traffic, without WattNot"} if wo else None
    return {"source": "live", "label": s["energy_wh"]["label"] if s else "MEASURED", "without": without,
            "simulated_traffic": any(str(r.get("run") or "").startswith("sim-") for r in recs),
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


def verify_receipt(savings_log, rid: str, key_path=None) -> dict | None:
    """Re-check a receipt: its gw_ id is sha256 over the record + previous id, and the previous id
    exists earlier in the log (hash chain). Signs the record digest offline (Ed25519) when a local
    signing key is configured; otherwise it says UNSIGNED. Never needs the network."""
    import hashlib
    recs = _read(savings_log)
    idx = next((i for i, r in enumerate(recs) if r.get("receipt_id") == rid), None)
    if idx is None:
        return None
    rec = recs[idx]
    body = {k: v for k, v in rec.items() if k != "receipt_id"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    id_ok = "gw_" + digest[:12] == rid
    prev = rec.get("prev_receipt_id", "")
    chain_ok = (prev == "" and idx == 0) or (idx > 0 and recs[idx - 1].get("receipt_id") == prev)
    out = {"receipt_id": rid, "sha256": digest, "id_matches": id_ok, "chain_ok": chain_ok,
           "position": idx + 1, "of": len(recs), "signer": "UNSIGNED", "signature": None}
    from wattnot.receipt.sign import local_key_path
    key = key_path or local_key_path()
    if key:
        from cryptography.hazmat.primitives import serialization
        from wattnot.receipt.sign import key_id
        k = serialization.load_pem_private_key(Path(key).read_bytes(), password=None)
        out.update(signer=f"offline:{key_id(k.public_key())}", signature=k.sign(bytes.fromhex(digest)).hex())
    return out


def quality(savings_log, monitor: dict | None) -> dict:
    """Quality gate results + joules per correct answer, from the gateway's own records.
    'Correct' = the shadow pair agreed with stock (the gateway has no answer key): ESTIMATE."""
    recs = [r for r in _read(savings_log) if r.get("status") == 200]
    mon = monitor or {}
    pairs = mon.get("pairs") or 0
    agree = mon.get("agreement")
    j = sum((r.get("actual") or {}).get("joules", 0) for r in recs)
    jb = sum((r.get("baseline") or {}).get("joules", 0) for r in recs)
    n_ok = round(len(recs) * agree) if agree is not None else None
    usd = sum((r.get("actual") or {}).get("usd", 0) for r in recs)
    return {"requests": len(recs), "shadow_pairs": pairs, "agreement": agree,
            "gate_off": [{"saver": e.get("saver"), "why": e.get("why")} for e in ((mon.get("gate") or {}).get("off") or [])],
            "j_per_correct": round(j / n_ok, 1) if n_ok else None,
            "j_per_correct_stock": round(jb / len(recs), 1) if recs else None,
            "j_label": (recs[-1]["labels"]["joules"] if recs else "ESTIMATE") + "; correct = agreed with stock on shadow pairs (ESTIMATE)",
            "spend_usd": round(usd, 6), "spend_label": "ESTIMATE (list price)",
            "spend_log": [{"receipt_id": r.get("receipt_id"), "ts": r.get("ts"), "usd": (r.get("actual") or {}).get("usd", 0)}
                          for r in recs[-20:]][::-1]}
