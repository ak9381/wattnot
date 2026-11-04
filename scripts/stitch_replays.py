"""Build stitched replays: one replay per platform from its recorded rounds.

Rule: union of all blocks. A block in more than one round uses the later valid take.
Every event gets replay.source_round and replay.source_date; original labels are kept.
Run: python scripts/stitch_replays.py
"""
from __future__ import annotations
import json, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RP = ROOT / "replays"
HK = Path("/workspace/hackathon")


def events(rid):
    return [json.loads(l) for l in (RP / rid / "events.jsonl").read_text().splitlines() if l.startswith("{")]


def tag(e, rnd, date, block):
    e = dict(e); r = dict(e.get("replay") or {})
    r.update(source_round=rnd, source_date=date, block=block, label="REPLAY"); e["replay"] = r
    return e


def block_of(rid, e):
    run = str(e.get("run") or "")
    if "langchain" in run: return "langchain"
    if "eye-hearing" in run: return "eye+hearing"
    if "ablate" in run: return "attribution"
    if rid.startswith("baseten"): return "main round (" + ("public gsm8k+humaneval" if rid.endswith("r1") else "own set") + ")"
    return "main round"


def stitch_pair(new, rounds, title, provider):
    out = RP / new
    if out.exists(): shutil.rmtree(out)
    out.mkdir()
    by_block, order = {}, []
    for rid in rounds:  # later rounds overwrite earlier ones
        evs = events(rid)
        date = min(e["ts"] for e in evs if isinstance(e.get("ts"), str))[:10]
        groups = {}
        for e in evs: groups.setdefault(block_of(rid, e), []).append(tag(e, rid, date, block_of(rid, e)))
        for b, g in groups.items():
            if b not in order: order.append(b)
            by_block[b] = (rid, date, g)
    with (out / "events.jsonl").open("w") as f:
        for b in order:
            for e in by_block[b][2]: f.write(json.dumps(e) + "\n")
    last = rounds[-1]
    for fn in ("receipt.json", "receipt.sigstore.json", "energy-receipt.json", "energy-receipt.sigstore.json", "eu_report.html", "round_eu_report.html"):
        if (RP / last / fn).exists(): shutil.copy2(RP / last / fn, out / fn)
    for rid in rounds[:-1]:
        if (RP / rid / "receipt.json").exists(): shutil.copy2(RP / rid / "receipt.json", out / f"receipt-{rid}.json")
    for rid in rounds:
        if (RP / rid / "blocks").is_dir(): shutil.copytree(RP / rid / "blocks", out / "blocks", dirs_exist_ok=True)
    blocks = [{"block": b, "source_round": by_block[b][0], "date": by_block[b][1], "events": len(by_block[b][2])} for b in order]
    dropped = [{"block": b, "source_round": rid, "reason": "replaced by the later take"} for rid in rounds for b in {block_of(rid, e) for e in events(rid)} if by_block[b][0] != rid]
    man = {"replay": new, "title": title, "provider": provider, "label": "REPLAY", "stitched_from": rounds,
           "rule": "union of all blocks; a block in both rounds uses the later valid take; each event carries replay.source_round",
           "headline_receipt": f"{last}/receipt.json", "blocks": blocks, "replaced": dropped,
           "model": json.loads((RP / last / "manifest.json").read_text()).get("model")}
    (out / "manifest.json").write_text(json.dumps(man, indent=1))
    return man


# Nov 4 H100 one-lever takes: latest valid receipt per workload (power cap 700 -> 375 W).
H100_TAKES = {  # seq-20261104-050330-text (gateway dir) is bogus and is not used
    "text": "rcpt_20261104-032655-text", "agent": "rcpt_20261104-034921-agent",
    "images (SAM test set)": "rcpt_20261104-013308-images", "voice": "rcpt_20261104-040850-voice"}


def stitch_h100():
    new, base = "h100-stitched", "h100-max-1103"
    out = RP / new
    if out.exists(): shutil.rmtree(out)
    shutil.copytree(RP / base, out)
    evs = [tag(e, base, "2026-11-03", "h100-max-1103 sweep and blocks") for e in events(base)]
    blocks = [{"block": b, "source_round": base, "date": "2026-11-03"} for b in json.loads((RP / base / "manifest.json").read_text())["blocks"]]
    seq = max((e.get("seq") or 0) for e in evs) + 1
    run_id = "h100-1104-takes"
    for name, rc in H100_TAKES.items():
        r = json.loads((ROOT / "receipts/h100-1104" / f"{rc}.json").read_text()); j = r["joules"]; ts = r["created_at"]
        sid = f"nov4-{name.split()[0]}"
        d = {"name": f"H100 take {name}: power cap {r['baseline']['params']['power_cap_w']} -> {r['candidate']['params']['power_cap_w']} W",
             "label": "MEASURED", "j_per_req_without": j["baseline"]["j_per_answer"], "j_per_req_with": j["candidate"]["j_per_answer"],
             "energy_change_pct": j["change_pct"]["j_per_answer"], "receipt": f"receipts/h100-1104/{rc}.json", "run": r["run_id"]}
        for typ in ("step.started", "step.finished"):
            evs.append(tag({"schema_version": "wattnot.event/1", "run_id": run_id, "seq": seq, "ts": ts, "type": typ, "step_id": sid, "data": d}, r["run_id"], "2026-11-04", f"Nov 4 take: {name}")); seq += 1
        blocks.append({"block": f"Nov 4 take: {name}", "source_round": r["run_id"], "date": "2026-11-04", "receipt": f"receipts/h100-1104/{rc}.json",
                       "j_per_req": [j["baseline"]["j_per_answer"], j["candidate"]["j_per_answer"]], "energy_change_pct": j["change_pct"]["j_per_answer"], "label": "MEASURED"})
    lt = json.loads((RP / base / "lever-takes-1104.json").read_text())
    import datetime as D
    latest = {}
    for e in lt["events"]:
        if e.get("step") == "answer_check": latest[e["lever"]] = e
    for lever, e in latest.items():
        ts = D.datetime.fromtimestamp(e["ts"], D.timezone.utc).isoformat()
        d = {"name": f"H100 take lever {lever}", "label": "MEASURED", "accepted": e["accepted"], "energy_change_pct": e["energy_change_pct"],
             "p99_change_pct": e["p99_change_pct"], "answers_same": e["answers_same"], "why_not": e.get("why_not", []), "decision": "kept" if e["accepted"] else "rolled back"}
        for typ in ("step.started", "step.finished"):
            evs.append(tag({"schema_version": "wattnot.event/1", "run_id": run_id, "seq": seq, "ts": ts, "type": typ, "step_id": f"nov4-lever-{lever}", "data": d}, "h100-1104 one-lever takes", "2026-11-04", f"Nov 4 lever: {lever}")); seq += 1
        blocks.append({"block": f"Nov 4 lever: {lever}", "source_round": "h100-1104 one-lever takes", "date": "2026-11-04", "label": "MEASURED",
                       "energy_change_pct": e["energy_change_pct"], "decision": d["decision"], "source": lt["source"]})
    with (out / "events.jsonl").open("w") as f:
        for e in evs: f.write(json.dumps(e) + "\n")
    man = json.loads((RP / base / "manifest.json").read_text())
    man.update(replay=new, title="H100 stitched: h100-max-1103 + Nov 4 H100 takes", provider="e2e", label="REPLAY",
               stitched_from=[base, "receipts/h100-1104", "lever-takes-1104.json"], blocks=blocks,
               excluded=[{"run": "seq-20261104-050330-text", "reason": "bogus take, not used"}],
               rule="union of all blocks; latest valid take per workload; each event carries replay.source_round")
    man.pop("files", None)
    (out / "manifest.json").write_text(json.dumps(man, indent=1))
    return man


if __name__ == "__main__":
    for m in (stitch_h100(), stitch_pair("modal-stitched", ["modal-r1", "modal-r2"], "Modal stitched: modal-r1 + modal-r2 (A100)", "modal"),
              stitch_pair("baseten-stitched", ["baseten-r1", "baseten-r2"], "Baseten stitched: baseten-r1 + baseten-r2", "baseten")):
        print(m["replay"]); [print("  ", b["block"], "|", b["source_round"], b["date"]) for b in m["blocks"]]
        for x in m.get("replaced", []): print("   replaced:", x)
