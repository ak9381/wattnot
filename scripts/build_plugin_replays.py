"""Build the three Nov 4 plugin runs under replays/ from the files recorded that day.

plugin-h100-1104   gateway takes on the H100 (receipts/h100-1104, NVML node log)
plugin-modal-1104  Modal A100 full-stage round r04 (events, signed receipts)
plugin-baseten-1104 gateway takes on Baseten (sequential results, node log; energy ESTIMATE)

Nothing is computed beyond unit changes (Wh -> J) and copying; every value keeps its label.
Power samples from the H100 node log are thinned to one every 2 s (selection only, noted).
"""
from __future__ import annotations
import datetime as dt, hashlib, json, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "replays"
H100_NODE = Path("/workspace/hackathon/h100-logs-1104-final2/wn-gw-1103/out/gateway/node.jsonl")
MODAL = Path("/workspace/wn-modaltake/data/modal-full-1101")
BT = Path("/workspace/wn-gw-cloud/out/hosted/baseten-1104")


def iso(t): return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat().replace("+00:00", "Z")
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def lab(s): return "ESTIMATE" if "ESTIMATE" in str(s or "") else ("MEASURED" if "MEASURED" in str(s or "") else (s or None))


def node_events(path, run, t0, t1, thin_s=2.0):
    out, last = [], None
    for raw in open(path):
        if not raw.startswith("{"): continue
        d = json.loads(raw); t = d.get("ts")
        if not isinstance(t, (int, float)) or not (t0 <= t <= t1): continue
        if d.get("kind") == "sample":
            if last is not None and t - last < thin_s: continue
            last = t
            out.append({"type": "telemetry.sample", "ts": iso(t), "run": run,
                        "data": {"power_w": d.get("power_w"), "cap_w": d.get("cap_w"), "label": d.get("label")}})
        else:
            data = {k: v for k, v in d.items() if k not in ("kind", "ts", "texts", "per_request_j")}
            data["label"] = lab(d.get("label"))
            data["summary"] = f"{d.get('step')}" + (f" ({d.get('arm')} arm)" if d.get("arm") else "")
            out.append({"type": f"gateway.{d.get('step')}", "ts": iso(t), "run": run, "data": data})
    return out


def write(name, man, events, files=()):
    d = OUT / name; d.mkdir(parents=True, exist_ok=True)
    for src, rel in files:
        (d / rel).parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src, d / rel)
    events.sort(key=lambda e: e.get("ts") or "")
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    man.update({"spec": "wattnot.replay-manifest/1", "name": name, "replay": name, "label": "REPLAY", "live_calls": False,
                "events": len(events), "files": [{"file": rel, "source": str(src), "sha256": sha(src)} for src, rel in files]})
    (d / "manifest.json").write_text(json.dumps(man, indent=1) + "\n")


def h100():
    takes, t0, t1 = [], None, None
    for p in sorted((ROOT / "receipts/h100-1104").glob("rcpt_*.json")):
        if p.name.endswith(".sigstore.json"): continue
        r = json.loads(p.read_text()); b, c = r["baseline"], r["candidate"]
        ws = r["subject"]["workload"]["name"]
        jb, jc = b["wh_per_req"]["mean"] * 3600, c["wh_per_req"]["mean"] * 3600
        takes.append({"workload": ws, "run_id": r["run_id"], "receipt": f"receipts/h100-1104/{p.name}",
                      "date": "2026-11-04", "created_at": r["created_at"],
                      "j_per_req": [round(jb, 1), round(jc, 1)], "energy_change_pct": round((jc / jb - 1) * 100, 2),
                      "p_value": r["result"]["welch"]["p_value"], "cap_w": c["params"].get("power_cap_w"),
                      "quality": [f"{b['quality'].get('correct')}/{b['quality'].get('scored')}", f"{c['quality'].get('correct')}/{c['quality'].get('scored')}"] if b["quality"].get("scored") else None,
                      "quality_why": None if b["quality"].get("scored") else "no graded answers recorded for this take",
                      "j_samples": [[x * 3600 for x in b["wh_per_req"]["samples"]], [x * 3600 for x in c["wh_per_req"]["samples"]]],
                      "label": "MEASURED"})
    starts = [dt.datetime.strptime(t["run_id"][4:19], "%Y%m%d-%H%M%S").replace(tzinfo=dt.timezone.utc).timestamp() for t in takes]
    ends = [dt.datetime.fromisoformat(t["created_at"]).timestamp() for t in takes]
    ev = node_events(H100_NODE, "plugin h100 1104", min(starts) - 120, max(ends) + 60)
    write("plugin-h100-1104", {"title": "Plugin run, H100 (Nov 4): live traffic through the WattNot gateway, one take per workload",
        "provider": "e2e", "gpu": "H100 80GB, E2E Networks", "model": "meta-models/Muse-Glimmer-30B", "date": "2026-11-04",
        "kind": "gateway-takes", "energy_label": "MEASURED", "headline": "seq-20261104-000651-text",
        "headline_why": "text take with the strongest test (Welch p 0.027); other takes are listed beside it, not averaged",
        "takes": takes, "receipt_signed": True,
        "gaps": ["p99 was not recorded by the gateway sequential runner (receipts say 0 is a placeholder).",
                 f"Power samples from {H100_NODE.name} thinned to one every 2 s for playback; the receipts hold the full counter energy."]}, ev)


def modal():
    r4 = MODAL / "r04"
    ev = []
    for p in (MODAL / "r04.without.events.jsonl", MODAL / "r04.with.events.jsonl"):
        ev += [json.loads(l) | {"replay": {"label": "REPLAY", "source": "modal r04 (Nov 4)"}} for l in open(p) if l.startswith("{")]
    files = [(r4 / "features/receipt.json", "receipt.json"), (r4 / "features/receipt.sigstore.json", "receipt.sigstore.json"),
             (r4 / "sweep/rcpt_20261101_modal_full_r04.json", "energy-receipt.json"),
             (r4 / "sweep/rcpt_20261101_modal_full_r04.sigstore.json", "energy-receipt.sigstore.json"),
             (r4 / "features/eu_report.html", "eu_report.html"), (r4 / "round.json", "round.json"), (r4 / "stage_entry.json", "stage_entry.json"),
             (r4 / "langchain/langchain_pass.json", "blocks/langchain/langchain_pass.json")]
    write("plugin-modal-1104", {"title": "Plugin run, Modal A100 (Nov 4): full-stage round r04 through the WattNot plug-in",
        "provider": "modal", "gpu": "A100", "model": "meta-models/Muse-Glimmer-30B", "date": "2026-11-04", "kind": "round",
        "receipt_signed": True, "source": str(r4),
        "gaps": ["Run from the dashboard as a full-stage round (r04), not as separate gateway takes; the 6:33 AM gateway attempt wrote no results (NVML cap refused).",
                 "LangChain block held quality but used +19,946 J; FP8 was rejected (+39.5% J/correct)."]}, ev, files)


def baseten():
    takes, files = [], []
    for f, ws, note in (("seq-text.json", "text", None), ("seq-agent.json", "agent", None), ("seq-images.json", "images", None),
                        ("seq-voice.json", "voice (take 1)", "broken take: the without arm recorded 0 J, so no saving is reported"),
                        ("seq-voice-b.json", "voice (take 2)", "agreement 0.2: most answers were 'no context' refusals")):
        d = json.loads((BT / f).read_text()); w0, w1 = d["without"], d["with"]
        n0, n1 = w0.get("requests") or 0, w1.get("requests") or 0
        jb = w0["joules"] / n0 if n0 and w0.get("joules") else None
        jc = w1["joules"] / n1 if n1 and w1.get("joules") else None
        ok = d.get("energy_saved_pct") is not None
        takes.append({"workload": ws, "run_id": d["run_id"], "file": f"takes/{f}", "date": "2026-11-04",
                      "j_per_req": [round(jb, 1) if jb else None, round(jc, 1) if jc else None] if ok else [None, None],
                      "energy_change_pct": -d["energy_saved_pct"] if ok else None, "agreement": d.get("agreement"),
                      "usd": [w0.get("usd"), w1.get("usd")], "label": d.get("energy_label") or "ESTIMATE",
                      "why": note, "j_samples": [[x[1] for x in w0.get("per_request_j") or []], [x[1] for x in w1.get("per_request_j") or []]] if ok else None})
        files.append((BT / f, f"takes/{f}"))
    files += [(BT / "savings.gate.jsonl", "savings.gate.jsonl"), (BT / "node.jsonl", "node.jsonl")]
    ev = node_events(BT / "node.jsonl", "plugin baseten 1104", 0, 1e12)
    write("plugin-baseten-1104", {"title": "Plugin run, Baseten (Nov 4): live traffic through the WattNot gateway, one take per workload",
        "provider": "baseten", "model": "Muse Spark open (Baseten)", "date": "2026-11-04", "kind": "gateway-takes",
        "energy_label": "ESTIMATE", "headline": "seq-20261104-140109-text",
        "headline_why": "text take (agreement 0.95); other takes are listed beside it, not averaged",
        "takes": takes, "receipt_signed": False,
        "gaps": ["Baseten is a hosted API with no GPU counter: energy is tokens x J/token per model (ESTIMATE).",
                 "No signed receipt was written for these takes; the sequential result files are copied as recorded.",
                 "The power cap step is skipped on this provider (no cap available)."]}, ev, files)


if __name__ == "__main__":
    h100(); modal(); baseten(); print("ok")
