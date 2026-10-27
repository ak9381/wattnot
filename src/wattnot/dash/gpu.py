"""Turn the real H100 results folder into replay scenes for the live GPU panel.

Every sample is one second of one run: ``t_s, power_w, cap_w, p99_ms, rps, tok_s, j_per_tok``.
A field the run did not measure is ``None`` and the screen shows a dash, never a made-up value.

Scenes (all from the H100 kit):

* ``flex-<event>``  the load-shed test. Power, cap, p99 and req/s are read each second.
* ``sweep-<model>`` the power-cap sweep, one trace per cap. Power is MEASURED each second; J/token
  and p99 come from that cap's sweep row (MEASURED, averaged over its repeats).
* ``adaptive``      the adaptive top-k test, normal vs adaptive, J/token and tok/s per run.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

LABEL = "MEASURED"


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def downsample(rows: list[dict], hz: float = 10.0) -> list[tuple[float, float]]:
    """(t_s, mean power_w) per whole second from a 10 Hz NVML trace."""
    buckets: dict[int, list[float]] = {}
    for r in rows:
        p, t = _f(r.get("power_w")), _f(r.get("t_s"))
        if p is None or t is None:
            continue
        buckets.setdefault(int(t), []).append(p)
    return [(float(s), sum(v) / len(v)) for s, v in sorted(buckets.items())]


def sample(t, power, cap=None, p99=None, rps=None, tok_s=None, jtok=None, extra=None):
    d = {"t_s": t, "power_w": None if power is None else round(power, 1), "cap_w": cap,
         "p99_ms": None if p99 is None else round(p99, 1),
         "rps": None if rps is None else round(rps, 2),
         "tok_s": None if tok_s is None else round(tok_s, 1),
         "j_per_tok": None if jtok is None else round(jtok, 4)}
    if extra:
        d.update(extra)
    return d


def _flex_note(receipt: Path, ev: dict, pts: list[dict]) -> str:
    """One plain sentence about what this shed event did, taken from its receipt and trace."""
    base = f"Asked for {ev.get('target_pct')}% less power."
    try:
        out = json.loads(receipt.read_text())["flex"]["outcome"]
    except (OSError, KeyError, ValueError):
        return base
    event = [p for p in pts if p.get("phase") == "event"]
    low_cap = min((p["cap_w"] for p in event if p.get("cap_w") is not None), default=None)
    low_w = min((p["power_w"] for p in event), default=None)
    went = (f" The cap went down to {low_cap:.0f} W and power to {low_w:.0f} W at the lowest."
            if low_cap is not None and low_w is not None else "")
    stop = f" Then the {out.get('limit')} check stopped it and the cap went back to " \
           f"{out.get('settled_cap_w')} W." if out.get("limit") else ""
    return (f"{base}{went}{stop} Settled result: {out.get('delivered_pct')}% delivered "
            f"(status: {out.get('status')}).")


def flex_scenes(main: Path) -> list[dict]:
    out = []
    for tj in sorted((main / "flex").glob("*.trace.json")):
        d = json.loads(tj.read_text())
        ev = d.get("event", {})
        pts = d["points"]
        samples = [sample(p["t_s"], p["power_w"], p["cap_w"], p["p99_ms"], p["throughput_rps"],
                          extra={"phase": p["phase"], "pass_rate": p.get("pass_rate"),
                                 "target_w": p.get("target_w")}) for p in pts]
        out.append({
            "id": f"flex-{ev.get('id', tj.stem)}",
            "title": f"Load shed: ask for {ev.get('target_pct')}% less power",
            "kind": "flex", "label": d.get("label", LABEL), "samples": samples,
            "note": _flex_note(main / tj.name.replace(".trace.json", ".json"), ev, pts),
            "receipt": tj.name.replace(".trace.json", ".json"),
        })
    return out


def sweep_scenes(main: Path) -> list[dict]:
    rows = _read_csv(main / "sweep.csv")
    by_model: dict[str, list[dict]] = {}
    for r in rows:
        by_model.setdefault(r["model"], []).append(r)
    traces: dict[tuple[str, int], Path] = {}
    for p in (main / "traces").glob("m-*/*-cap*-rep1.csv"):
        m = re.match(r"(.+)-cap(\d+)-rep1\.csv", p.name)
        if m:
            traces[(m.group(1), int(m.group(2)))] = p
    out = []
    for model, rs in by_model.items():
        short = model.split("/")[-1]
        key = "muse-glimmer" if "Glimmer" in short else "llama-3.1-8b"
        samples, t0 = [], 0.0
        base = next((_f(r["wh_per_1k_tok"]) for r in rs if r["gate"] == "baseline"), None)
        for r in rs:
            cap = int(float(r["cap_w"]))
            path = traces.get((key, cap))
            if not path:
                continue
            jtok = _f(r["wh_per_1k_tok"]) * 3.6  # Wh per 1000 tokens -> J per token
            for t, w in downsample(_read_csv(path)):
                samples.append(sample(t0 + t, w, cap, _f(r["p99_s"]) * 1000, None, None, jtok,
                                      {"gate": r["gate"], "model": short}))
            t0 = samples[-1]["t_s"] + 1 if samples else t0
        out.append({"id": f"sweep-{key}", "title": f"Power-cap sweep: {short}", "kind": "sweep",
                    "label": LABEL, "samples": samples, "baseline_j_per_tok":
                    None if base is None else round(base * 3.6, 4),
                    "note": "Every lower cap failed the quality check in this run (see gate)."})
    return out


def adaptive_scene(rerun: Path) -> list[dict]:
    p = rerun / "adaptive_topk" / "adaptive_topk_runs.json"
    if not p.is_file():
        return []
    d = json.loads(p.read_text())
    samples, t0, runs = [], 0.0, []
    for run in sorted(d["runs"], key=lambda r: (r["rep"], r["arm"])):
        csvp = rerun.parent / run["csv"].split("results/", 1)[-1]
        if not csvp.is_file():
            csvp = next(rerun.glob("traces/*/" + Path(run["csv"]).name), csvp)
        if not csvp.is_file():
            continue
        toks_s = run["tokens"] / run["seconds"]
        for t, w in downsample(_read_csv(csvp)):
            samples.append(sample(t0 + t, w, None, None, None, toks_s, run["j_per_token"],
                                  {"arm": run["arm"], "rep": run["rep"],
                                   "active_experts": run["active_experts"]}))
        t0 = samples[-1]["t_s"] + 1
        runs.append({k: run[k] for k in ("arm", "rep", "wh", "seconds", "tokens", "j_per_token",
                                         "active_experts")})
    return [{"id": "adaptive", "title": f"Adaptive top-k on and off: {d['model'].split('/')[-1]}",
             "kind": "adaptive", "label": d.get("label", LABEL), "samples": samples, "runs": runs,
             "note": "Five repeats per arm on one H100. A small model and few repeats."}]


def j_per_token_by_arm(rerun: Path) -> dict | None:
    """Mean measured J/token for each adaptive arm (used to price captured tokens)."""
    p = rerun / "adaptive_topk" / "adaptive_topk_runs.json"
    if not p.is_file():
        return None
    d = json.loads(p.read_text())
    out = {}
    for arm in ("off", "on"):
        v = [r["j_per_token"] for r in d["runs"] if r["arm"] == arm]
        if v:
            out[arm] = sum(v) / len(v)
    out["n_tokens_per_run"] = d["runs"][0]["tokens"]
    out["model"] = d["model"]
    out["runs_per_arm"] = len([r for r in d["runs"] if r["arm"] == "off"])
    out["source"] = "H100 NVML energy counter, adaptive top-k test"
    out["label"] = "MEASURED"
    return out


def audit_state(audit: Path) -> dict[str, dict]:
    """Last known state per proposal from an audit log (read only)."""
    st: dict[str, dict] = {}
    if not audit.is_file():
        return st
    for line in audit.read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        s = st.setdefault(e["proposal_id"], {"events": []})
        s["events"].append({k: e.get(k) for k in ("seq", "ts", "event", "actor", "via")})
        s["status"] = e["event"]
    return st


def approvals(*dirs: Path) -> list[dict]:
    out = []
    for d in dirs:
        state = audit_state(d / "audit.jsonl")
        for p in sorted(list(d.glob("prop_*.json")) + list((d / "proposals").glob("*.json"))):
            prop = json.loads(p.read_text())
            s = state.get(prop["id"], {"events": [], "status": "proposed"})
            out.append({"id": prop["id"], "kind": prop.get("kind"), "title": prop.get("title"),
                        "summary": prop.get("summary"), "created_at": prop.get("created_at"),
                        "expires_at": prop.get("expires_at"), "status": s["status"],
                        "events": s["events"], "bounds": prop.get("change", {}).get("bounds"),
                        "savings": prop.get("savings", [])})
    return out


def receipts(*dirs: Path) -> list[dict]:
    out = []
    for d in dirs:
        for p in sorted(d.rglob("rcpt_*.json")):
            if p.name.endswith((".sigstore.json", ".trace.json")):
                continue
            r = json.loads(p.read_text())
            res = r.get("result", {})
            out.append({
                "id": r["receipt_id"], "profile": r.get("profile"),
                "model": r.get("subject", {}).get("model"), "created_at": r.get("created_at"),
                "saving_pct": (res.get("saving_pct") or {}).get("mean"),
                "basis": (res.get("saving_pct") or {}).get("basis"),
                "grade": r.get("grade"), "signed": p.with_suffix(".sigstore.json").is_file(),
                "baseline_wh": (r.get("baseline", {}).get("wh_per_req") or {}).get("mean"),
                "candidate_wh": (r.get("candidate", {}).get("wh_per_req") or {}).get("mean"),
                "label": (r.get("measurement") or {}).get("label", LABEL),
            })
    return out


def build_bundle(results: Path) -> dict:
    """One JSON the server can ship: scenes, approvals, receipts and the J/token pricing."""
    main = next((d for d in sorted(results.glob("h100-*")) if (d / "sweep.csv").is_file()), None)
    rerun = next((d for d in sorted(results.glob("h100-*adaptive*")) if d.is_dir()), None)
    summary = json.loads((main / "summary.json").read_text()) if main else {}
    scenes = []
    if main:
        scenes += flex_scenes(main) + sweep_scenes(main)
    if rerun:
        scenes += adaptive_scene(rerun)
    dirs = [d for d in (main, rerun) if d]
    return {
        "source": "results folder of the H100 Oct 30 run (NVIDIA H100 80GB, E2E Networks, Delhi)",
        "gpu": summary.get("gpu"), "plan": summary.get("plan"),
        "scenes": scenes,
        "approvals": approvals(*[d / "approvals" for d in dirs]),
        "receipts": receipts(*dirs),
        "pricing": j_per_token_by_arm(rerun) if rerun else None,
    }
