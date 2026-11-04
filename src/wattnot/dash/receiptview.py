"""Trimmed view of a signed proof receipt for the Results screen.

Pure mapping, no I/O except `load_receipt`. Only numbers, labels, ids and the
hash leave this module: never the key (the receipt holds none) and never
prompt text (free-text reasons are dropped, not copied).
"""

from __future__ import annotations

import json
from pathlib import Path

from wattnot.dash.arch import PRICE, WATER

SPEC = "wattnot.proof-receipt/1"
OTHER_SPECS = ("wattnot.receipt/1",)   # sweep receipts; round receipts carry `kind`, no spec


def load_any(path: Path) -> dict:
    """A proof receipt, else a sweep (`wattnot.receipt/1`) or round (`kind` + `round`) receipt.

    Raises ValueError when the file is none of these or has no integrity hash."""
    try:
        return load_receipt(path)
    except ValueError as first:
        try:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise first from None
        integ = doc.get("integrity") if isinstance(doc, dict) else None
        known = isinstance(doc, dict) and (doc.get("spec") in OTHER_SPECS
                                            or (doc.get("kind") and doc.get("round")))
        if not known or not isinstance(integ, dict) or not isinstance(integ.get("sha256"), str):
            raise first from None
        return doc


def _lab(v):
    """(value, label) from a {value, label} cell or a bare number."""
    if isinstance(v, dict):
        return v.get("value", v.get("mean")), v.get("label")
    return v, None


def summary_view(doc: dict) -> dict:
    """A short view of a sweep or round receipt: ids, signature, hash and the headline numbers
    exactly as the receipt states them (no new numbers are computed here)."""
    integ = doc.get("integrity") or {}
    facts = []
    if doc.get("spec") in OTHER_SPECS:
        res = doc.get("result") or {}
        sav = (res.get("saving_pct") or {})
        lab = (doc.get("measurement") or {}).get("label")
        if sav.get("mean") is not None:
            facts.append({"name": "Energy saved (mean)", "value": sav["mean"], "unit": "%", "label": lab})
            facts.append({"name": "95% interval low", "value": sav.get("ci_low"), "unit": "%", "label": lab})
            facts.append({"name": "95% interval high", "value": sav.get("ci_high"), "unit": "%", "label": lab})
        model = (doc.get("subject") or {}).get("model")
        rid, run, label = doc.get("receipt_id"), doc.get("run_id"), doc.get("grade")
    else:
        res = doc.get("results") or {}
        for key, name, unit in (("energy_saved_pct", "Energy saved", "%"),
                                ("j_per_correct_saved_pct", "Energy per correct answer saved", "%"),
                                ("j_per_correct_without", "Energy per correct answer, without WattNot", "J"),
                                ("j_per_correct_with", "Energy per correct answer, with WattNot", "J"),
                                ("accuracy_without", "Accuracy without WattNot", ""),
                                ("accuracy_with", "Accuracy with WattNot", "")):
            if key in res:
                v, lab = _lab(res[key])
                if isinstance(v, (int, float)):
                    facts.append({"name": name, "value": v, "unit": unit,
                                  "label": lab or (doc.get("labels") or {}).get("energy")})
        model, rid, run, label = doc.get("model"), f"{doc.get('kind')}:{doc.get('round')}", doc.get("round"), None
    return {
        "kind": "summary",
        "receipt_id": rid, "run_id": run, "model": model, "label": label,
        "spec": doc.get("spec") or doc.get("kind"),
        "facts": facts,
        "hash": integ.get("sha256"),
        "signature": {"signed": integ.get("signer") is not None,
                      "signer": integ.get("signer"), "rekor": integ.get("rekor")},
        "steps": [],
    }


def view_any(doc: dict) -> dict:
    return view(doc) if doc.get("spec") == SPEC else summary_view(doc)


def find_receipt(events_file: Path) -> Path:
    """The receipt in the run folder: `receipt.json`, else the newest `proof-*.json`.

    Looks in the events file's own folder first, then up to two levels up (a
    run writes the receipt at the out root while events sit in a `runs/<id>/`
    or `live/` subfolder). Raises FileNotFoundError when there is none.
    """
    folder = Path(events_file).parent
    for level in (folder, folder.parent, folder.parent.parent):
        exact = level / "receipt.json"
        if exact.is_file():
            return exact
        proofs = sorted(f for f in level.glob("proof-*.json")
                        if not f.name.endswith(".sigstore.json"))
        if proofs:
            return proofs[-1]
        # a stitched replay (replays/h100-max-1103) keeps one signed proof per block in proofs/<block>/
        nested = sorted((f for f in level.glob("proofs/*/proof-*.json")
                         if not f.name.endswith(".sigstore.json")),
                        key=lambda f: (f.stat().st_mtime, f.as_posix()))
        if nested:
            return nested[-1]
    raise FileNotFoundError(f"no receipt next to {events_file}")


def load_receipt(path: Path) -> dict:
    """Read and check a receipt file. Raises ValueError when it is not one."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"not a readable receipt: {e}") from None
    if not isinstance(doc, dict) or doc.get("spec") != SPEC:
        raise ValueError(f"not a {SPEC} receipt")
    integ = doc.get("integrity")
    if (not isinstance(doc.get("receipt_id"), str) or not isinstance(integ, dict)
            or not isinstance(integ.get("sha256"), str)):
        raise ValueError("receipt is missing its id or hash")
    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("receipt has no steps")
    return doc


def _mean(xs) -> float | None:
    xs = [x for x in (xs or []) if isinstance(x, (int, float))]
    return sum(xs) / len(xs) if xs else None


def view(doc: dict) -> dict:
    """The trimmed view the Results screen shows."""
    steps = doc["steps"]
    want = doc.get("best_cap_w")
    best = next((s for s in steps if s.get("cap_w") == want), None) if want is not None else None
    kept = best is not None
    per_1m = lambda j: {  # noqa: E731
        "usd": j / 3.6 * PRICE["usd_per_kwh"],
        "litres": j / 3.6 * WATER["factor_l_per_kwh"],
    } if j is not None else None
    if kept:
        before_j, after_j = _mean(best.get("j_per_req_stock")), _mean(best.get("j_per_req_cap"))
        # a rehearsal receipt is MOCK end to end, whatever its step rows say
        mock = doc.get("label") == "MOCK" or doc.get("mode") == "rehearsal"
        tag = "MOCK" if mock else best.get("label")
        p99 = best.get("p99_s") or {}
        qual = best.get("quality") or {}
        saved = (best.get("energy_change_pct") or {}).get("mean")
        change = (best.get("p99_change_pct") or {}).get("mean")
    else:
        pooled = [x for s in steps for x in (s.get("j_per_req_stock") or [])
                  if isinstance(x, (int, float))]
        before_j = after_j = sum(pooled) / len(pooled) if pooled else None
        tag, p99, qual, saved, change = None, {}, {}, 0.0, 0.0
    b, a = per_1m(before_j), per_1m(after_j)
    integ = doc.get("integrity") or {}
    return {
        "receipt_id": doc.get("receipt_id"),
        "run_id": doc.get("run_id"),
        "model": doc.get("model"),
        "label": doc.get("label"),
        "created": doc.get("created"),
        "kept": kept,
        "kept_cap_w": doc.get("best_cap_w"),
        "energy_j_per_req": {"before": before_j, "after": after_j,
                             "saved_pct": saved, "label": tag},
        "cost_usd_per_1m": {"before": (b or {}).get("usd"), "after": (a or {}).get("usd"),
                            "label": "ESTIMATE", "formula": PRICE["formula"],
                            "source": PRICE["source"]},
        "water_l_per_1m": {"before": (b or {}).get("litres"), "after": (a or {}).get("litres"),
                           "label": "ESTIMATE", "formula": WATER["formula"],
                           "source": WATER["source"]},
        "p99_s": {"before": p99.get("stock"), "after": p99.get("cap"),
                  "change_pct": change, "label": tag},
        "quality_pct": {"stock": qual.get("stock_pct"), "cap": qual.get("cap_pct"),
                        "kept": bool(kept and (qual.get("cap_pct") is not None)),
                        "label": tag},
        "hash": integ.get("sha256"),
        "signature": {"signed": integ.get("signer") is not None,
                      "signer": integ.get("signer"), "rekor": integ.get("rekor")},
        "steps": [{"cap_w": s.get("cap_w"), "accepted": bool(s.get("accepted")),
                   "label": s.get("label"), "chosen_by": s.get("chosen_by")}
                  for s in steps],
        "planner": {"name": (doc.get("planner") or {}).get("name"),
                    "model": (doc.get("planner") or {}).get("model")},
    }
