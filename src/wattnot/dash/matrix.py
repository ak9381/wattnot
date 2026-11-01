"""Run matrix view for the dashboard: the 12 planned rounds, Spark-vs-deterministic grades,
the Meta spend meter (split across the 8 Meta models) and the provider credit meters.

Read-only. Reads scripts/e2e/round-matrix.json (the plan), result files under
<root>/out/matrix/<round id>.json (written by a real run; none exist yet) and the Meta spend
ledger summary. A round with no result file is PLANNED. Nothing here calls a model or spends.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "scripts" / "e2e" / "round-matrix.json"

# The 8 Meta models, matched to ledger model ids by prefix (order = the dashboard strip).
MODELS = [
    ("muse-spark", "Muse Spark", ("muse-spark-1",)),
    ("muse-wonder", "Muse Wonder", ("muse-wonder",)),
    ("muse-glimmer", "Muse Glimmer 30B", ("muse-glimmer",)),
    ("sam-3.1", "SAM 3.1", ("sam-",)),
    ("muse-voice", "Muse Voice", ("muse-voice",)),
    ("muse-code", "Muse Code", ("muse-code",)),
    ("muse-spark-open", "Muse Spark Open", ("muse-spark-open",)),
    ("muse-image", "Muse Image", ("muse-image",)),
]


def _model_key(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("muse-spark-open"):
        return "muse-spark-open"
    for key, _, prefixes in MODELS:
        if any(m.startswith(p) for p in prefixes):
            return key
    return "other"


def meta_meter(entries: list[dict], cap: float, soft: float, credits: float) -> dict:
    per = {k: {"name": n, "usd": 0.0, "calls": 0} for k, n, _ in MODELS}
    per["other"] = {"name": "Other", "usd": 0.0, "calls": 0}
    for e in entries:
        p = per[_model_key(e.get("model", ""))]
        p["usd"] = round(p["usd"] + float(e.get("usd") or 0), 6)
        p["calls"] += 1
    if not per["other"]["calls"]:
        del per["other"]
    total = round(sum(p["usd"] for p in per.values()), 6)
    return {"label": "MEASURED", "source": "Meta spend ledger (billed token counts at list price)",
            "total_usd": total, "cap_usd": cap, "soft_usd": soft, "credits_usd": credits,
            "warning": total >= soft, "per_model": per}


def _ledger_entries(path: Path | None) -> list[dict]:
    from wattnot.meta.ledger import SpendLedger
    try:
        return SpendLedger(path).entries()
    except Exception:   # a missing or unreadable ledger is shown as empty, never a 500
        return []


def grade_summary(answers: list[dict]) -> dict:
    """answers: [{id, dataset, deterministic: bool, spark: bool|None}]. Deterministic decides."""
    graded = [a for a in answers if a.get("spark") is not None]
    dis = [a for a in graded if bool(a["spark"]) != bool(a.get("deterministic"))]
    return {"answers": len(answers), "spark_graded": len(graded),
            "correct": sum(bool(a.get("deterministic")) for a in answers),
            "agreement": (round(1 - len(dis) / len(graded), 4) if graded else None),
            "disagreements": [{k: a.get(k) for k in ("id", "dataset", "arm", "deterministic", "spark", "spark_reason")}
                              for a in dis][:200]}


def build(root: Path | None = None, config: Path | None = None, ledger: Path | None = None) -> dict[str, Any]:
    root = Path(root or ROOT)
    cfg = json.loads(Path(config or CONFIG).read_text(encoding="utf-8"))
    rdir = root / "out" / "matrix"
    rounds = []
    for r in cfg["rounds"]:
        f = rdir / f"{r['id']}.json"
        row = dict(r, status="PLANNED", label="PLANNED", results=None, grades=None)
        if f.is_file():
            try:
                res = json.loads(f.read_text(encoding="utf-8"))
                row.update(status=res.get("status", "done"), label=res.get("label", "MEASURED"),
                           results=res.get("results"),
                           grades=grade_summary(res.get("answers", [])) if res.get("answers") else None)
            except (OSError, ValueError):
                row.update(status="unreadable", label="PLANNED")
        rounds.append(row)
    b = cfg["meta_budget"]
    return {"spec": "wattnot.dash.matrix/1", "approved": cfg.get("approved"),
            "datasets": cfg["datasets"], "spark_grade_all": cfg.get("spark_grade_all", False),
            "deterministic_decides": cfg.get("deterministic_decides", True),
            "spark_grade_note": cfg.get("spark_grade_note", ""),
            "rounds": rounds,
            "meta": dict(meta_meter(_ledger_entries(ledger), b["cap_usd"], b["soft_usd"], b["credits_usd"]),
                         matrix_estimate_usd=b.get("matrix_estimate_usd"),
                         per_model_estimate=b.get("per_model_estimate", {})),
            "providers": cfg["providers"]}
