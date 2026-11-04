"""Recorded runs under replays/<id>/, listed for the dashboard's "Replay a run" picker.

Everything is read from disk (manifest.json + events.jsonl); nothing is hardcoded and no
network call or GPU is needed. Each event keeps its own label (MEASURED, ESTIMATE, MOCK).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_PLATFORM = {"modal": "Modal", "baseten": "Baseten", "e2e": "H100", "h100": "H100"}


def _events(path: Path) -> list[dict]:
    out = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if raw.startswith("{"):
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return out


def _summary(d: Path) -> dict | None:
    ev_file, man_file = d / "events.jsonl", d / "manifest.json"
    if not ev_file.is_file():
        return None
    man = json.loads(man_file.read_text()) if man_file.is_file() else {}
    evs = _events(ev_file)
    labels = Counter((e.get("data") or {}).get("label") for e in evs)
    labels = {k: v for k, v in labels.items() if k in ("MEASURED", "ESTIMATE", "SIMULATED", "MOCK")}
    ts = sorted(e["ts"] for e in evs if isinstance(e.get("ts"), str))
    host = next(((e.get("data") or {}).get("host") or {} for e in evs if e.get("type") == "run.started"), {})
    prov = (man.get("provider") or host.get("provider") or d.name.split("-")[0]).lower()
    platform = _PLATFORM.get(prov, prov.title())
    gpu = man.get("gpu") or host.get("gpu")
    if platform == "Modal" and not gpu:
        m = re.search(r"\((A100|H100)[^)]*\)", man.get("title", ""))
        gpu = m.group(1) if m else None
    title = man.get("title") or " · ".join(x for x in (gpu, man.get("model")) if x) or d.name
    return {
        "id": d.name, "platform": platform, "gpu": gpu, "title": title,
        "date": ts[0][:10] if ts else None, "started": ts[0] if ts else None,
        "finished": ts[-1] if ts else None, "events": len(evs),
        "power_samples": sum(1 for e in evs if e.get("type") == "telemetry.sample"
                             and (e.get("data") or {}).get("power_w") is not None),
        "labels": labels, "measured": labels.get("MEASURED", 0) > 0,
        "gaps": man.get("gaps", []), "receipt_signed": bool(man.get("receipt_signed")) or (d / "proofs").is_dir(),
        "cli": f"./run-local.sh replay {d.name}",
    }


def list_replays(root: Path) -> list[dict]:
    base = Path(root) / "replays"
    if not base.is_dir():
        return []
    rows = [s for d in sorted(base.iterdir()) if d.is_dir() and _ID.match(d.name) and (s := _summary(d))]
    return sorted(rows, key=lambda r: (r["platform"] != "H100", r["id"]))


def events_path(root: Path, rid: str) -> Path | None:
    if not _ID.match(rid or ""):
        return None
    p = Path(root) / "replays" / rid / "events.jsonl"
    return p if p.is_file() else None


def load_events(root: Path, rid: str) -> list[dict] | None:
    p = events_path(root, rid)
    return None if p is None else _events(p)
