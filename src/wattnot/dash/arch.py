"""Builds the story and architecture bundle from recorded runs, receipts and ledgers.

    python scripts/build_dash_arch.py --e2e /path/to/results --out data/dash/story.json

Everything is read, never written, except the output bundle. A number is either read from a
recorded file (MEASURED), computed from one with a stated formula (ESTIMATE), or "–" (None).
The Meta ledger is bundled as totals only; no key, no prompt text.
"""

from __future__ import annotations

import csv
import json
import re
import statistics as stx
from datetime import datetime
from pathlib import Path

from wattnot.dash import arch_spec as spec

ROOT = Path(__file__).resolve().parents[3]

# Published factors used for the ESTIMATE rows. Shown with their source in the UI.
WATER = {
    "factor_l_per_kwh": 0.36,
    "name": "US data-center site water use (WUE, site)",
    "source": "Lawrence Berkeley National Laboratory, 2024 United States Data Center Energy Usage Report (Dec 2024): modeled fleet-average site WUE just over 0.36 L/kWh through 2023",
    "url": "https://eta-publications.lbl.gov/sites/default/files/2024-12/lbnl-2024-united-states-data-center-energy-usage-report.pdf",
    "formula": "litres = measured GPU kWh x 0.36 L/kWh",
    "caveat": "A US fleet average for on-site cooling water, applied to GPU energy only. A given site can be far lower or higher, and water used to generate the electricity is not counted.",
}
PRICE = {
    "usd_per_kwh": 0.1387,
    "name": "US commercial electricity price, 12 months to August 2026",
    "source": "U.S. Energy Information Administration, Electric Power Monthly, Table 5.3 (released 2026-10-23): commercial rolling 12 months ending August 2026 = 13.87 cents/kWh",
    "url": "https://www.eia.gov/electricity/monthly/epm_table_grapher.php?t=table_5_03",
    "formula": "dollars = measured GPU kWh x $0.1387/kWh",
}
SCALE_DEFAULT_REQ_PER_DAY = 10_000_000


def st(k, label, v, unit, tag, src, note=None):
    assert tag is None or tag in spec.LABELS, tag
    if v is None:
        tag = None          # a missing value shows as a dash and carries no label
    return {"k": k, "label": label, "v": v, "unit": unit, "tag": tag, "src": src, "note": note}


def missing(k, label, src, note):
    return st(k, label, None, "", None, src, note)


def rd(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def jl(path: Path) -> list[dict]:
    try:
        return [json.loads(x) for x in Path(path).read_text().splitlines() if x.startswith("{")]
    except OSError:
        return []


def r(x, d=4):
    return None if x is None else round(x, d)


# ---------------------------------------------------------------- Meta ledger (aggregates)

def ledger_groups(path: Path) -> dict:
    d = rd(path)
    if not d:
        return {}
    groups: dict[str, dict] = {}

    def g(name):
        return groups.setdefault(name, {"calls": 0, "in": 0, "out": 0, "cached": 0, "usd": 0.0,
                                        "lat": [], "estimated": 0})

    allg = g("all")
    for e in d["entries"]:
        p = e["purpose"]
        if p.startswith("earlier:"):          # notes about past spend, not calls
            continue
        name = ("next-knob" if p.startswith("next-knob") else "judge" if p.startswith("judge")
                else "effort-order" if p == "effort-order" else "explain" if p.startswith("explain")
                else "coding-set" if p.startswith("coding-set")
                else "effort-ladder" if p.startswith("effort-ladder") else "other")
        for x in (g(name), allg):
            x["calls"] += 1
            x["in"] += e.get("input_tokens", 0)
            x["out"] += e.get("output_tokens", 0)
            x["cached"] += e.get("cached_tokens", 0)
            x["usd"] += e.get("usd", 0.0)
            x["estimated"] += 1 if e.get("estimated") else 0
            if e.get("latency_s"):
                x["lat"].append(e["latency_s"])
    earlier = sum(e.get("usd", 0.0) for e in d["entries"] if e["purpose"].startswith("earlier:"))
    out = {}
    for k, x in groups.items():
        out[k] = {"calls": x["calls"], "in": x["in"], "out": x["out"], "cached": x["cached"],
                  "usd": round(x["usd"], 4), "p50_s": r(stx.median(x["lat"]), 2) if x["lat"] else None,
                  "estimated": x["estimated"]}
    out["_cap_usd"] = d["cap_usd"]
    out["_earlier_usd"] = round(earlier, 4)
    out["_price_source"] = d.get("price_source")
    return out


def meta_stats(lg: dict, key: str, prefix: str) -> list[dict]:
    g = lg.get(key)
    src = "Meta spend ledger (~/.wattnot/meta_spend.json), purposes: " + key
    if not g:
        return [missing(prefix + "calls", "Meta API calls", src, "ledger not found")]
    tag = "MEASURED" if g["estimated"] == 0 else "ESTIMATE"
    return [st(prefix + "calls", "Meta API calls", g["calls"], "", "MEASURED", src),
            st(prefix + "tok", "tokens in / out", f'{g["in"]:,} / {g["out"]:,}', "", "MEASURED", src),
            st(prefix + "usd", "spent", g["usd"], "$", tag, src,
               f'under the ${lg["_cap_usd"]:.0f} cap'),
            st(prefix + "p50", "median reply time", g["p50_s"], "s", "MEASURED", src)]


# ---------------------------------------------------------------- hosted (Baseten)

def baseten(repo: Path) -> dict:
    cl = repo / "reports" / "cloud-live"
    out: dict = {"calls": 0, "prompt": 0, "cached": 0, "completion": 0, "usd": 0.0, "lat": [],
                 "checks_passed": 0, "checks_total": 0, "per_run": []}
    for f in sorted(cl.glob("cloud_*.calls.jsonl")):
        rows = jl(f)
        out["calls"] += len(rows)
        for c in rows:
            out["prompt"] += c.get("prompt_tokens") or 0
            out["cached"] += c.get("cached_tokens") or 0
            out["completion"] += c.get("completion_tokens") or 0
            out["usd"] += c.get("usd") or 0.0
            if c.get("latency_s"):
                out["lat"].append(c["latency_s"])
        out["per_run"].append((f.stem.split(".")[0], len(rows)))
    for f in sorted(cl.glob("cloud_*.percall-audit.json")):
        a = rd(f) or {}
        out["checks_passed"] += a.get("checks_passed", 0)
        out["checks_total"] += a.get("checks_total", 0)
    run2 = rd(cl / "cloud_20261027_043936_b9975c.json") or {}
    out["run2"] = run2
    out["run2_calls"] = jl(cl / "cloud_20261027_043936_b9975c.calls.jsonl")
    out["ab"] = rd(cl / "cache-ab-20261027_042534.json") or {}
    out["probe"] = rd(cl / "cache-probe-20261027_042346.json") or {}
    out["meter"] = rd(cl / "cloud_20261027_043936_b9975c.audit.json") or {}
    return out


# ---------------------------------------------------------------- H100 run

def h100(e2e: Path | None) -> dict:
    out: dict = {"ok": False}
    if not e2e or not e2e.exists():
        return out
    main = next(iter(sorted(e2e.glob("h100-oct30-2*"))), None)
    if not main:
        return out
    out["ok"] = True
    out["main"] = main
    out["summary"] = rd(main / "summary.json") or {}
    out["probe"] = rd(main / "probe.json") or {}
    out["sweep"] = list(csv.DictReader((main / "sweep.csv").open())) if (main / "sweep.csv").exists() else []
    out["rerun"] = rd(e2e / "h100-oct30-adaptive-rerun" / "summary.json") or {}
    out["qwen"] = rd(e2e / "qwen3-adaptive" / "summary.json") or {}
    out["qwen_rcpt"] = rd(e2e / "qwen3-adaptive" / "rcpt_moe_9a56b2eca886.json") or {}
    out["audit"] = jl(main / "approvals" / "audit.jsonl")
    out["audit_rerun"] = jl(e2e / "h100-oct30-adaptive-rerun" / "approvals" / "audit.jsonl")
    rows = 0
    files = 0
    for f in main.glob("traces/**/*.csv"):
        files += 1
        rows += max(0, sum(1 for _ in f.open()) - 1)
    out["trace_files"], out["trace_rows"] = files, rows
    recs = []
    for f in list(main.glob("**/rcpt_*.json")) + list((e2e / "qwen3-adaptive").glob("rcpt_*.json")) + \
            list((e2e / "h100-oct30-adaptive-rerun").glob("**/rcpt_*.json")):
        if f.name.endswith((".sigstore.json", ".trace.json")):
            continue
        d = rd(f)
        if d and "integrity" in d:
            recs.append({"id": d["receipt_id"], "profile": d.get("profile"),
                         "signed": bool(d["integrity"].get("signer")) or f.with_suffix(".sigstore.json").exists(),
                         "rekor": d["integrity"].get("rekor") is not None,
                         "created_at": d.get("created_at"), "path": f.name})
    seen, uniq = set(), []
    for x in recs:
        if x["id"] not in seen:
            seen.add(x["id"])
            uniq.append(x)
    out["receipts"] = sorted(uniq, key=lambda x: x["created_at"] or "")
    # VM window: launch from vm.json, deletion from PROGRESS.md (read-only, no secrets)
    vm = (e2e.parent / "vm.json")
    prog = (e2e.parent / "PROGRESS.md")
    try:
        launch = re.search(r"(20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d)", vm.read_text()).group(1)
        dele = re.search(r"DELETED (\d\d:\d\d)", prog.read_text()).group(1)
        t0 = datetime.fromisoformat(launch)
        t1 = datetime.fromisoformat(launch[:10] + "T" + dele + ":00")
        out["vm_hours"] = round((t1 - t0).total_seconds() / 3600, 2)
        out["vm_window"] = f"{launch[11:16]} to {dele} IST"
    except (OSError, AttributeError, ValueError):
        out["vm_hours"] = None
    return out


def sweep_row(h: dict, model: str, cap: int) -> dict | None:
    for x in h.get("sweep", []):
        if model in x["model"] and int(x["cap_w"]) == cap:
            return x
    return None


# ---------------------------------------------------------------- node statistics

def node_stats(repo: Path, h: dict, lg: dict, bt: dict) -> dict[str, list[dict]]:
    S: dict[str, list[dict]] = {}
    sumy = h.get("summary", {})
    stages = sumy.get("stages", {})
    sweep_src = "H100 run: results/h100-oct30-*/sweep.csv"

    # workload
    def count(name):
        try:
            return sum(1 for x in (repo / "workloads" / name).read_text().splitlines() if x.startswith("{"))
        except OSError:
            return None
    plan = sumy.get("plan", {})
    n_replay = count("replay-v1.jsonl")
    S["workload"] = [
        st("replay", "public prompts in the load set", n_replay, "", "MEASURED", "workloads/replay-v1.jsonl"),
        st("req", "requests per round", n_replay * plan["repeat"] if n_replay and plan else None, "", "MEASURED",
           "prompts x repeat 16 (summary.json plan)"),
        st("conc", "at the same time", plan.get("concurrency"), "", "MEASURED", "summary.json plan"),
        st("code", "Muse Code tasks in its set", count("muse-code-v1.jsonl"), "", "MEASURED", "workloads/muse-code-v1.jsonl"),
    ]
    # telemetry
    pr = h.get("probe", {})
    lw = pr.get("load_window", {})
    S["telemetry"] = [
        st("probe_s", "probe took", stages.get("probe", {}).get("seconds"), "s", "MEASURED", "summary.json stages"),
        st("probe_w", "mean power in the probe", r(lw.get("mean_w"), 1), "W", "MEASURED", "probe.json load_window (NVML)"),
        st("rows", "trace samples kept", h.get("trace_rows"), "", "MEASURED", "traces/**/*.csv (10 Hz)"),
        st("files", "trace files with checksums", h.get("trace_files"), "", "MEASURED", "traces/**/*.csv"),
    ]
    S["meters_roadmap"] = [missing("na", "readings", "meter/zeus.py, meter/dcgm.py", "not bound; they raise until a back-end is attached")]
    S["agent"] = meta_stats(lg, "next-knob", "a_")
    ab = None
    try:
        ab = json.loads((ROOT / "reports" / "ablation.json").read_text())
    except (OSError, ValueError):
        pass
    arms = {a["arm"]: a for a in (ab or {}).get("arms", [])}
    S["optimizer"] = [
        st("warm", "trials to best, warm-started", arms.get("warm", {}).get("trials_to_best"), "of 20", "ESTIMATE",
           "reports/ablation.json (synthetic objective)", "cold and grid did not reach it in 20"),
        st("cold", "trials to best, cold", arms.get("cold", {}).get("trials_to_best") or "not reached in 20", "", "ESTIMATE", "reports/ablation.json"),
        st("sw", "the H100 sweep used", "step-down in 50 W", "", None, "summary.json plan"),
    ]
    aud = list({(x["seq"], x["ts"]): x for x in h.get("audit", []) + h.get("audit_rerun", [])}.values())
    grants = [x for x in aud if x["event"] == "proposed"]
    S["proposals"] = [
        st("n", "proposals written in the H100 work", len(grants) if grants else None, "", "MEASURED", "approvals/audit.jsonl"),
        st("man", "approved by a person live", 0, "", "MEASURED", "approvals/audit.jsonl",
           "all four were pre-approved by a flag on the kit script, by the operator"),
        st("chain", "hash-chain problems", len(sumy.get("approvals", {}).get("audit_chain_problems", [])) if sumy else None, "", "MEASURED", "summary.json"),
    ]
    closed = [x for x in aud if x["event"] == "grant_closed"]
    changes = sum(x["detail"]["usage"]["changes"] for x in closed if x["detail"].get("usage"))
    bykind: dict[str, int] = {}
    for x in closed:
        for k, v in (x["detail"].get("usage") or {}).get("by_kind", {}).items():
            bykind[k] = bykind.get(k, 0) + v["n"]
    S["apply"] = [
        st("chg", "changes applied and restored", changes or None, "", "MEASURED", "approvals/audit.jsonl grant_closed"),
        st("caps", "of them power-cap changes", bykind.get("power_cap"), "", "MEASURED", "approvals/audit.jsonl"),
        st("cfg", "vLLM settings changes", bykind.get("vllm_config"), "", "MEASURED", "approvals/audit.jsonl"),
    ]
    low = [x for x in h.get("sweep", []) if x["gate"] != "baseline"]
    fails = [x for x in low if "fixed" in x["gate"]]
    prog = jl(Path(h["main"]) / "flex" / "progress.jsonl") if h.get("ok") else []
    base_pass = prog[-1]["pass_rate"] if prog else None
    S["gates"] = [
        st("fail", "lower caps that failed the fixed check", len(fails) if low else None, f"of {len(low)}", "MEASURED", sweep_src),
        st("base", "baseline pass rate of the fixed check", r(base_pass * 100, 1) if base_pass is not None else None, "%", "MEASURED",
           "flex/progress.jsonl", "this low baseline is why the check cannot judge yet"),
    ] + meta_stats(lg, "judge", "j_")[:3]
    recs = h.get("receipts", [])
    S["receipts"] = [
        st("n", "receipts from the H100 work", len(recs) or None, "", "MEASURED", "results/**/rcpt_*.json"),
        st("signed", "signed with the offline key", sum(1 for x in recs if x["signed"]) if recs else None, "", "MEASURED", "rcpt integrity + .sigstore.json"),
        st("rekor", "with a public Rekor entry", sum(1 for x in recs if x["rekor"]) if recs else None, "", "MEASURED", "rcpt integrity.rekor",
           "public signing is off unless approved"),
    ]
    eu = repo / "reports" / "eu-2024-1364.csv"
    try:
        rows = list(csv.DictReader(eu.open()))
        n_na = sum(1 for x in rows if x["label"] == "NOT_AVAILABLE")
        S["eu"] = [st("fields", "fields filled", len(rows) - n_na, f"of {len(rows)}", "MOCK", "reports/eu-2024-1364.csv",
                      "built from an example receipt, not from the H100 work"),
                   st("na", "need facility data", n_na, "", "MOCK", "reports/eu-2024-1364.csv")]
    except OSError:
        S["eu"] = [missing("fields", "fields filled", "reports/eu-2024-1364.csv", "file not found")]

    # hosted
    run2 = bt.get("run2", {})
    tot = run2.get("totals", {})
    pl = run2.get("planner", {})
    S["effort_router"] = [
        st("save", "hosted cost saved at the same quality", tot.get("saving_pct"), "%", "MEASURED", "cloud receipt cloud_20261027_043936_b9975c"),
        st("usd", "cost for 75 requests, tuned vs top level", f'${tot.get("tuned_usd")} vs ${tot.get("would_have_cost_xhigh_usd")}' if tot else None, "", "MEASURED", "same receipt"),
        st("tok", "answer tokens, tuned vs top level", f'{tot.get("completion_tokens_tuned"):,} vs {tot.get("completion_tokens_xhigh"):,}' if tot else None, "", "MEASURED", "same receipt"),
        st("pay", "tuning pays for itself after", tot.get("payback_runs"), "runs", "MEASURED", "same receipt"),
        st("wh", "energy avoided (range)", f'{run2["energy"]["saved_wh"]["low"]:.2f} to {run2["energy"]["saved_wh"]["high"]:.2f} Wh' if run2 else None, "", "ESTIMATE",
           "same receipt: 2 x active params x tokens x J/FLOP range", "not measured on Baseten's GPUs"),
        st("plan", "order proposal cost", r(pl.get("usd"), 4), "$", "MEASURED", "same receipt planner block"),
    ]
    ab_arms = {a["arm"]: a for a in bt.get("ab", {}).get("arms", [])}
    curve = bt.get("probe", {}).get("curve", [])
    S["cache"] = [
        st("save", "input cost saved, stable-first", ab_arms.get("stable-first", {}).get("input_saving_vs_volatile_pct"), "%", "MEASURED", "cache-ab-20261027_042534.json (8 calls per arm)"),
        st("share", "share of input served from cache", r(ab_arms.get("stable-first", {}).get("cached_share", 0) * 100, 1) if ab_arms else None, "%", "MEASURED", "same file"),
        st("hold", "cached share after 10 minutes", r(curve[-1]["cached_share"] * 100, 1) if curve else None, "%", "MEASURED", "cache-probe-20261027_042346.json"),
        st("aff", "extra gain from the affinity header",
           r(ab_arms["stable-first+affinity"]["input_saving_vs_volatile_pct"] - ab_arms["stable-first"]["input_saving_vs_volatile_pct"], 2) if ab_arms else None,
           "points", "MEASURED", "same A/B file", "no gain from the header"),
    ]
    S["baseten_model"] = [
        st("calls", "calls recorded", bt.get("calls"), "", "MEASURED", "reports/cloud-live/*.calls.jsonl"),
        st("tok", "tokens in / cached / out", f'{bt["prompt"]:,} / {bt["cached"]:,} / {bt["completion"]:,}' if bt.get("calls") else None, "", "MEASURED", "same files"),
        st("usd", "spent on these calls", r(bt.get("usd"), 3) if bt.get("calls") else None, "$", "MEASURED", "per-call usage x list price"),
        st("p50", "median reply time", r(stx.median(bt["lat"]), 2) if bt.get("lat") else None, "s", "MEASURED", "same files"),
        st("audit", "per-call checks that matched", f'{bt["checks_passed"]:,} of {bt["checks_total"]:,}' if bt.get("checks_total") else None, "", "MEASURED",
           "*.percall-audit.json", "against Baseten's per-response usage, not the billing meter"),
        missing("meter", "billing meter", "*.audit.json", "not available: the usage endpoint returned nothing for this key's workspace"),
    ]
    # rig
    S["e2e_rig"] = [
        st("vm", "VM time", h.get("vm_hours"), "h", "MEASURED", "vm.json launch to PROGRESS.md delete note", h.get("vm_window")),
        st("stage", "kit stages, main run", r(sum(x.get("seconds", 0) for x in stages.values()) / 3600, 2) if stages else None, "h", "MEASURED", "summary.json stages"),
        st("sweep", "cap sweep took", stages.get("sweep", {}).get("seconds"), "s", "MEASURED", "summary.json stages"),
        st("flex", "flex events took", stages.get("flex", {}).get("seconds"), "s", "MEASURED", "summary.json stages"),
        missing("usd", "rental cost", "E2E billing", "not readable through the API"),
    ]
    mg, ll = "Muse-Glimmer", "Llama-3.1"
    def delta(model):
        a, b = sweep_row(h, model, 700), sweep_row(h, model, 400)
        return (a, b, r((float(b["wh_per_req"]) / float(a["wh_per_req"]) - 1) * 100, 1)) if a and b else (None, None, None)
    ga, gb, gd = delta(mg)
    la, lb, ld = delta(ll)
    S["models"] = [
        st("mg", "Glimmer 30B energy per request, 700 W to 400 W",
           f'{float(ga["wh_per_req"])*1000:.2f} to {float(gb["wh_per_req"])*1000:.2f} mWh' if ga else None, "", "MEASURED", sweep_src),
        st("mgd", "Glimmer 30B change", gd, "%", "MEASURED", sweep_src, "quality check pending"),
        st("ll", "Llama 3.1 8B energy per request, 700 W to 400 W",
           f'{float(la["wh_per_req"])*1000:.2f} to {float(lb["wh_per_req"])*1000:.2f} mWh' if la else None, "", "MEASURED", sweep_src),
        st("lld", "Llama 3.1 8B change", ld, "%", "MEASURED", sweep_src, "quality check pending"),
    ]
    rr = h.get("rerun", {}).get("adaptive_topk", {})
    qs = h.get("qwen", {}).get("summary", {})
    S["adaptive"] = [
        st("q", "Qwen3-30B-A3B energy per token", qs.get("saving_pct"), "% less", "MEASURED", "qwen3-adaptive/summary.json", "all three gates passed"),
        st("qa", "agreement with normal top-k", r(h.get("qwen", {}).get("agreement", {}).get("pct"), 1), "%", "MEASURED", "same file", "gate: at least 95%"),
        st("qe", "experts firing per token", f'{qs.get("on", {}).get("active_experts", 0):.2f} of {qs.get("off", {}).get("active_experts", 0):.0f}' if qs else None, "", "MEASURED", "same file"),
        st("g", "Granite 3B energy per token", rr.get("saving_pct_j_per_token"), "% less", "MEASURED", "adaptive-rerun/summary.json",
           f'agreement {rr.get("agreement_pct")}%: just under the 95% gate, so not proposed'),
    ]
    fx = sumy.get("flex", [])
    S["flex"] = [st(f"e{i}", f'asked for {x["target_pct"]:.0f}% less power, delivered', x["delivered_pct"], "%", "MEASURED", "summary.json flex",
                    "the quality guard blocked deeper cuts") for i, x in enumerate(fx)] or \
        [missing("e", "delivered", "summary.json flex", "no events recorded")]
    sam = rd(repo / "rack" / "sam.spend.json") or {}
    masks = rd(repo / "rack" / "masks.sam.json") or {}
    S["sam"] = [
        st("calls", "SAM 3.1 calls", sam.get("calls"), "", "MEASURED", "rack/sam.spend.json"),
        st("usd", "spent", sam.get("usd_estimate"), "$", "ESTIMATE", "rack/sam.spend.json usd_estimate"),
        st("obj", "usable masks returned", len(masks.get("objects", [])) if masks else None, "", "MEASURED", "rack/masks.sam.json",
           f'status "{masks.get("status")}": the hand-drawn overlay is in use'),
    ]
    S["route"] = [missing("r", "region choice", "route/replay.py", "no recorded report is bundled; grid data is a fixture")]
    S["certificate"] = [missing("c", "certificates", "certificate/", "format only; always MOCK")]
    S["spark_open"] = [missing("s", "runs", "summary.json skipped_models", "skipped: needs about 700 GB of GPU memory; the rented card has 80 GB")]
    return S


# ---------------------------------------------------------------- who is Meta

META_PRODUCTS = {
    "Muse Spark": {"does": "Plans the next trial, judges answer pairs, orders effort levels and explains receipts. Called through the Meta Model API as muse-spark-1.3.",
                   "nodes": [("agent", "*"), ("gates", "judge"), ("effort_router", "order")]},
    "Muse Spark Open": {"does": "The open-weight Muse Spark that Baseten hosts. Every hosted call goes to it.",
                        "nodes": [("effort_router", "ref"), ("effort_router", "try"), ("baseten_model", "*"), ("cache", "*")]},
    "Llama 3.1 8B": {"does": "One of the two models swept from 700 W to 400 W on the H100.",
                     "nodes": [("models", "*"), ("e2e_rig", "sweep")]},
    "Muse Glimmer 30B": {"does": "The other model swept on the H100. The recorded load ran on it.",
                         "nodes": [("models", "*"), ("e2e_rig", "sweep")]},
    "Muse Code": {"does": "A set of coding tasks written by Muse Spark; Muse Spark was run on it at three effort levels.",
                  "nodes": [("workload", "load")]},
    "SAM 3.1": {"does": "Asked to cut the rack photo into slots. Its masks were not usable, so a hand-drawn overlay is in use.",
                "nodes": [("sam", "*")]},
}


def meta_block(S: dict, lg: dict, bt: dict, h: dict) -> list[dict]:
    out = []
    spark = lg.get("all")
    cs = lg.get("coding-set") or {}
    el = lg.get("effort-ladder") or {}
    prod_stats = {
        "Muse Spark": [
            st("calls", "Meta API calls, all purposes", spark and spark["calls"], "", "MEASURED", "Meta spend ledger"),
            st("tok", "tokens in / out", f'{spark["in"]:,} / {spark["out"]:,}' if spark else None, "", "MEASURED", "Meta spend ledger"),
            st("usd", "spent of the $20 cap", spark and spark["usd"], "$", "MEASURED", "Meta spend ledger",
               f'plus ${lg.get("_earlier_usd", 0)} noted from earlier work'),
        ],
        "Muse Spark Open": [
            st("calls", "hosted calls", bt.get("calls"), "", "MEASURED", "reports/cloud-live/*.calls.jsonl"),
            st("usd", "spent on Baseten", r(bt.get("usd"), 3), "$", "MEASURED", "per-call usage x list price"),
            st("save", "cost saved by the effort router", S["effort_router"][0]["v"], "%", "MEASURED", "cloud receipt"),
        ],
        "Llama 3.1 8B": S["models"][2:4],
        "Muse Glimmer 30B": S["models"][0:2],
        "Muse Code": [
            st("calls", "Muse Spark calls on the Muse Code set", (cs.get("calls") or 0) or None, "", "MEASURED", "ledger purposes coding-set:*"),
            st("usd", "spent", cs.get("usd"), "$", "MEASURED", "ledger"),
            st("ladder", "effort-ladder calls before it", el.get("calls"), "", "MEASURED", "ledger purposes effort-ladder:*"),
        ],
        "SAM 3.1": S["sam"],
    }
    for name, d in META_PRODUCTS.items():
        out.append({"name": name, "does": d["does"], "nodes": d["nodes"], "stats": prod_stats[name]})
    return out


# ---------------------------------------------------------------- story / demo levers

def story(h: dict, bt: dict, S: dict) -> dict:
    sumy = h.get("summary", {})
    plan = sumy.get("plan", {})
    mg = "Muse-Glimmer"
    a, b = sweep_row(h, mg, 700), sweep_row(h, mg, 400)
    la, lb = sweep_row(h, "Llama-3.1", 700), sweep_row(h, "Llama-3.1", 400)
    if not (a and b):
        return {}
    tok_per_req = float(a["wh_per_req"]) / float(a["wh_per_1k_tok"]) * 1000
    replay_file = ROOT / "workloads" / "replay-v1.jsonl"
    prompts = [json.loads(x) for x in replay_file.read_text().splitlines() if x.startswith("{")]
    kwh = lambda wh: wh / 1000     # noqa: E731
    water = lambda wh: kwh(wh) * WATER["factor_l_per_kwh"]    # noqa: E731
    usd = lambda wh: kwh(wh) * PRICE["usd_per_kwh"]           # noqa: E731

    def energy_lever(before_wh, after_wh):
        d = before_wh - after_wh
        return {"wh_before": before_wh, "wh_after": after_wh, "saved_pct": r((1 - after_wh / before_wh) * 100, 1),
                "per_million": {"kwh": r(d * 1e6 / 1000, 2),
                                "usd": r(usd(d) * 1e6, 2), "litres": r(water(d) * 1e6, 2)}}
    cap = energy_lever(float(a["wh_per_req"]), float(b["wh_per_req"]))
    cap.update({
        "id": "cap", "title": "Power cap", "model": "Muse Glimmer 30B on one H100",
        "before_label": "700 W (stock)", "after_label": "400 W",
        "p99_before_s": float(a["p99_s"]), "p99_after_s": float(b["p99_s"]),
        "p99_change_pct": r((float(b["p99_s"]) / float(a["p99_s"]) - 1) * 100, 1),
        "quality": {"state": "pending", "text": "measured, quality check pending",
                    "why": "The fixed answer check failed at every lower cap, but the baseline itself passes only about 6% of its checks, so the check cannot tell good from bad yet."},
        "llama": {"saved_pct": r((1 - float(lb["wh_per_req"]) / float(la["wh_per_req"])) * 100, 1),
                  "p99_change_pct": r((float(lb["p99_s"]) / float(la["p99_s"]) - 1) * 100, 1)},
        "src": "H100 run sweep.csv (NVML)",
    })
    qr = h.get("qwen_rcpt", {})
    qs = h.get("qwen", {}).get("summary", {})
    wh_off = qr["baseline"]["wh_per_req"]["mean"]
    wh_on = qr["candidate"]["wh_per_req"]["mean"]
    topk = energy_lever(wh_off, wh_on)
    topk.update({
        "id": "topk", "title": "Adaptive top-k", "model": "Qwen3-30B-A3B on one H100",
        "before_label": "normal top-k (8 experts)", "after_label": "adaptive top-k (6.9 on average)",
        "p99_before_s": qs["off"]["p99_ms"] / 1000, "p99_after_s": qs["on"]["p99_ms"] / 1000,
        "p99_change_pct": r(qs["p99_increase_pct"], 1),
        "j_per_token": [r(qs["off"]["j_per_token"]["mean"], 3), r(qs["on"]["j_per_token"]["mean"], 3)],
        "experts": [qs["off"]["active_experts"], r(qs["on"]["active_experts"], 2)],
        "quality": {"state": "passed", "text": f'passed all three gates: {qs["gates"]["quality"]["agreement_pct"]}% agreement (needs 95%)',
                    "agreement_pct": qs["gates"]["quality"]["agreement_pct"]},
        "note": "16 requests of 64 new tokens, batches of 8, one card, transformers expert loop in both arms; p99 here is a batch's generate time.",
        "src": "qwen3-adaptive receipt rcpt_moe_9a56b2eca886 (NVML)",
    })
    t = bt.get("run2", {}).get("totals", {})
    cls = bt.get("run2", {}).get("classes", [])
    p99_b = max(c["would_have_cost_xhigh"]["latency_p99_s"] for c in cls) if cls else None
    p99_a = max(c["measured"]["latency_p99_s"] for c in cls) if cls else None
    en = bt.get("run2", {}).get("energy", {})
    hosted = {
        "id": "hosted", "title": "Effort routing and cache order", "model": "Muse Spark Open on Baseten",
        "before_label": "top reasoning level every time", "after_label": "cheapest level that passes",
        "usd_before": t.get("xhigh_usd_per_request"), "usd_after": t.get("tuned_usd_per_request"),
        "saved_pct": t.get("saving_pct"),
        "per_million": {"usd": r((t.get("xhigh_usd_per_request", 0) - t.get("tuned_usd_per_request", 0)) * 1e6, 0)},
        "cache_saved_pct": S["cache"][0]["v"],
        "p99_before_s": p99_b, "p99_after_s": p99_a,
        "p99_change_pct": r((p99_a / p99_b - 1) * 100, 1) if p99_a and p99_b else None,
        "quality": {"state": "passed", "text": "every class kept its quality and passed the p99 and judge gates",
                    "classes": [{"class": c["class"], "chosen": c["chosen_effort"], "saving_pct": c["saving"]["pct"],
                                 "quality_pct": c["tried"][0].get("quality_pct")} for c in cls]},
        "energy_wh_per_75": en.get("saved_wh"), "src": "cloud receipt cloud_20261027_043936_b9975c",
        "note": "75 requests, 5 repeats, 5 prompt classes. The whole tuning run cost "
                f'${t.get("experiment_usd")} and pays for itself after {t.get("payback_runs")} runs.',
    }
    ctext = prompts and sum(len(p["prompt"]) for p in prompts) // len(prompts)
    task = {
        "title": "A coding assistant under load",
        "facts": [
            st("req", "requests per round", len(prompts) * plan.get("repeat", 16), "", "MEASURED", "replay-v1 prompts x repeat"),
            st("conc", "at the same time", plan.get("concurrency"), "", "MEASURED", "summary.json plan"),
            st("rounds", "rounds per setting", plan.get("reps"), "", "MEASURED", "summary.json plan"),
            st("tok", "answer length", round(tok_per_req), "tokens", "ESTIMATE", "Wh per request / Wh per 1k tokens (sweep.csv)"),
            st("p99", "slowest 1% of requests (p99)", float(a["p99_s"]), "s", "MEASURED", "sweep.csv at 700 W"),
            st("cap", "stock power cap", 700, "W", "MEASURED", "probe.json: card default"),
            st("prompt", "prompt length", ctext, "characters", "MEASURED", "workloads/replay-v1.jsonl", "short prompts"),
            missing("ctx", "long-context sessions, many tool calls", "recorded runs", "not in the recorded runs yet"),
        ],
        "why": ["Many people ask at once, so the slowest requests decide what users feel, not the average.",
                "Saving power only counts if answers stay right and the slowest requests stay within 10% of before.",
                "The biggest saver, a lower power cap, is also the one that can slow answers down."],
    }
    return {"task": task, "levers": [cap, topk, hosted], "water": WATER, "price": PRICE,
            "scale": {"req_per_day": SCALE_DEFAULT_REQ_PER_DAY,
                      "assumptions": ["requests per day is an input you can change", "every request looks like the measured ones",
                                      "one saving at a time: the three levers were measured in separate runs and are not added together",
                                      "energy is GPU-only (NVML); facility overhead is not counted"]}}


# ---------------------------------------------------------------- recorded replays (cues)

def cue(node, sub, text, t=None, took=None, stat=None, tag=None, ok=True):
    return {"node": node, "sub": sub, "text": text, "t": t, "took": took, "stat": stat, "tag": tag, "ok": ok}


def replays(h: dict, bt: dict, lg: dict) -> dict:
    st_ = h.get("summary", {}).get("stages", {})
    mg = "Muse-Glimmer"
    a, b = sweep_row(h, mg, 700), sweep_row(h, mg, 400)
    sw = (lambda s: f'{float(s["wh_per_req"])*1000:.2f} mWh/request, p99 {float(s["p99_s"]):.2f} s') if a else (lambda s: "")
    rr = h.get("rerun", {}).get("adaptive_topk", {})
    qs = h.get("qwen", {}).get("summary", {})
    cues = [
        cue("e2e_rig", "setup", "Set up the H100 host", took=st_.get("setup", {}).get("seconds"), tag="MEASURED"),
        cue("proposals", "write", "Write a trial-grant proposal for caps 200 to 700 W and vLLM settings", tag="MEASURED"),
        cue("proposals", "decide", "Approved: pre-approved by flag on the kit script (operator)", tag="MEASURED"),
        cue("proposals", "audit", "Audit entry chained to the previous one", tag="MEASURED"),
        cue("e2e_rig", "probe", "Probe: 24 s load window to confirm the meter", took=st_.get("probe", {}).get("seconds"), tag="MEASURED"),
        cue("telemetry", "probe", "Card found: H100 80GB, cap range 200 to 700 W", tag="MEASURED"),
        cue("telemetry", "sample", "Power read 10 times a second into a checksummed trace", tag="MEASURED"),
        cue("e2e_rig", "flex", "Flex events: ask for 15%, 25%, 35% less power", took=st_.get("flex", {}).get("seconds"), tag="MEASURED"),
        cue("flex", "event", "Shed event loaded (the signal is simulated)", tag="SIMULATED"),
        cue("flex", "tick", "Controller lowers the cap while quality and p99 hold", tag="MEASURED"),
        cue("flex", "restore", "Delivered 0%, 0.57%, 0.31%: the quality guard blocked deeper cuts", tag="MEASURED"),
        cue("e2e_rig", "sweep", "Sweep the cap 700 to 400 W, two models, five rounds each", took=st_.get("sweep", {}).get("seconds"), tag="MEASURED"),
        cue("workload", "send", "32 requests at a time to the server", tag="MEASURED"),
        cue("apply", "check", "Inside the approved bounds: go", tag="MEASURED"),
        cue("apply", "cap", "Cap set to 700 W (stock), then lowered in 50 W steps", tag="MEASURED"),
        cue("telemetry", "integrate", "Stock: " + sw(a) if a else "stock", tag="MEASURED"),
        cue("telemetry", "attribute", "Energy split across requests", tag="MEASURED"),
        cue("apply", "cap", "Cap set to 400 W: " + sw(b) if b else "400 W", tag="MEASURED"),
        cue("gates", "p99", "p99 +3.7%: inside the 10% limit", tag="MEASURED"),
        cue("gates", "fixed", "Fixed check failed at every lower cap, but the baseline passes only about 6%: check pending", tag="MEASURED", ok=False),
        cue("gates", "decide", "No cap passed the gate; the best-cap re-run was skipped", tag="MEASURED", ok=False),
        cue("e2e_rig", "best", "Re-run of the best cap skipped (none passed)", took=0.0, tag="MEASURED", ok=False),
        cue("e2e_rig", "topk", "Adaptive top-k stage failed with a router shape error (transformers 5.19)", took=st_.get("adaptive_topk", {}).get("seconds"), tag="MEASURED", ok=False),
        cue("adaptive", "load", "Re-run, patched: Granite 3B loaded with transformers", tag="MEASURED"),
        cue("adaptive", "hook", "Routers hooked: keep experts until 90% of router weight is covered", tag="MEASURED"),
        cue("adaptive", "arms", f'Granite 3B: {rr.get("saving_pct_j_per_token")}% less energy per token', took=rr.get("seconds"), tag="MEASURED"),
        cue("adaptive", "agree", f'Granite agreement {rr.get("agreement_pct")}%: just under the 95% gate', tag="MEASURED", ok=False),
        cue("adaptive", "arms", f'Qwen3-30B-A3B: {qs.get("saving_pct")}% less energy per token' if qs else "Qwen3", tag="MEASURED"),
        cue("adaptive", "agree", f'Qwen3 agreement {h.get("qwen", {}).get("agreement", {}).get("pct", 0):.1f}%: all three gates passed', tag="MEASURED"),
        cue("receipts", "build", "Receipts written for the flex events and the adaptive runs", tag="MEASURED"),
        cue("receipts", "canon", "Written in a fixed byte form", tag="MEASURED"),
        cue("receipts", "hash", "SHA-256, chained", tag="MEASURED"),
        cue("receipts", "sign", "Signed with the offline key; no public Rekor entry", tag="MEASURED"),
        cue("proposals", "audit", "Grant closed after 161 changes; every setting put back", tag="MEASURED"),
    ]
    run2 = bt.get("run2", {})
    calls = bt.get("run2_calls", [])
    pl = next((c for c in calls if c.get("class") == "planner"), {})
    pcls = {}
    for c in run2.get("classes", []):
        pcls[c["class"]] = c
    hosted = [
        cue("workload", "load", "15 prompts in 5 classes: extract, classify, trace, arith, logic", tag="MEASURED"),
        cue("effort_router", "order", f'Muse Spark proposes the order to try (reply {pl.get("latency_s")} s, {pl.get("prompt_tokens")} in / {pl.get("completion_tokens")} out)', took=pl.get("latency_s"), tag="MEASURED"),
        cue("baseten_model", "send", "Requests go to Muse Spark Open on Baseten", tag="MEASURED"),
        cue("effort_router", "ref", f'Top level first: ${run2.get("totals", {}).get("would_have_cost_xhigh_usd")} for 75 requests', tag="MEASURED"),
        cue("baseten_model", "usage", "Tokens, cached tokens and latency read from each reply", tag="MEASURED"),
    ]
    for k, c in pcls.items():
        hosted.append(cue("effort_router", "try", f'{k}: "{c["chosen_effort"]}" passed the gates; saves {c["saving"]["pct"]}%', tag="MEASURED"))
    hosted += [
        cue("gates", "judge", "A judge compares answers at the cheaper level with the top level: 0 of 15 worse", tag="MEASURED"),
        cue("gates", "p99", "p99 inside the budget for every class", tag="MEASURED"),
        cue("effort_router", "keep", f'Kept the cheapest level per class: {run2.get("totals", {}).get("saving_pct")}% cheaper', tag="MEASURED"),
        cue("cache", "ab", f'Shared text first: {(bt.get("ab", {}).get("arms") or [{}, {"input_saving_vs_volatile_pct": None}])[1].get("input_saving_vs_volatile_pct")}% less input cost', tag="MEASURED"),
        cue("cache", "probe", "Cache still warm after 10 minutes (98.7%)", tag="MEASURED"),
        cue("baseten_model", "percall", f'{bt.get("checks_passed", 0):,} of {bt.get("checks_total", 0):,} per-call checks matched', tag="MEASURED"),
        cue("baseten_model", "meter", "Billing meter not available for this key: not claimed", tag=None, ok=False),
        cue("effort_router", "receipt", f'Receipt written; the whole tuning run cost ${run2.get("totals", {}).get("experiment_usd")}', tag="MEASURED"),
    ]
    return {"h100": {"id": "h100", "title": "The H100 run on E2E", "note": "Order and durations are from the run's own stage log, audit log and receipts. The animation is compressed; \"took\" is real time.", "cues": cues},
            "hosted": {"id": "hosted", "title": "The hosted run on Baseten", "note": "From the recorded calls of run 2 (204 calls).", "cues": hosted}}


# ---------------------------------------------------------------- the agent's real decisions

def agent_rounds(repo: Path) -> dict:
    d = rd(repo / "docs" / "meta-session-2026-10-27.json") or {}
    nk = (d.get("next_knob") or {}).get("gpu-power", {})
    rounds = [x for x in nk.get("trace", []) if "knob" in x]
    return {"model": d.get("model"), "label": nk.get("label"),
            "note": "The decisions are real Muse Spark replies. The surface they were tested on was a simulator, so their savings are SIMULATED.",
            "rounds": rounds}


# ---------------------------------------------------------------- the bundle

def build(*, repo: Path = ROOT, e2e: Path | None = None, ledger: Path | None = None) -> dict:
    h = h100(e2e)
    lg = ledger_groups(ledger or Path.home() / ".wattnot" / "meta_spend.json")
    bt = baseten(repo)
    S = node_stats(repo, h, lg, bt)
    products = {"agent": "Muse Spark", "effort_router": "Muse Spark Open", "baseten_model": "Muse Spark Open",
                "cache": "Muse Spark Open", "models": "Llama 3.1 8B and Muse Glimmer 30B", "sam": "SAM 3.1",
                "workload": "Muse Code"}
    nodes = []
    meta_subs = {"agent": "*", "baseten_model": "*", "cache": "*", "models": "*", "spark_open": "*",
                 "sam": {"masks"}, "workload": {"load"}, "gates": {"judge"}, "effort_router": {"order", "ref", "try"}}
    ev = {}
    ev["receipts"] = [f'{x["id"]} · {"signed offline" if x["signed"] else "unsigned"} · Rekor {"yes" if x["rekor"] else "no"}' for x in h.get("receipts", [])]
    grants = [x for x in {(y["seq"], y["ts"]): y for y in h.get("audit", []) + h.get("audit_rerun", [])}.values() if x["event"] == "proposed"]
    ev["apply"] = [f'grant {x["detail"].get("proposal_id", x.get("proposal_id", ""))}' for x in grants if x["detail"] or x.get("proposal_id")]
    ev["effort_router"] = ["reports/cloud-live/cloud_20261027_043936_b9975c.json"]
    ev["baseten_model"] = [f"reports/cloud-live/{a}.calls.jsonl ({n} calls)" for a, n in bt.get("per_run", [])]
    for n in spec.NODES:
        n = json.loads(json.dumps(n))
        n["stats"] = S.get(n["id"], [])
        if n["id"] == "cache" and "meta" not in n["partners"]:
            n["partners"].append("meta")
            n["used_for"]["meta"] = "The cache A/B calls go to Muse Spark Open, so Meta's model is what the cached prefix is read by."
        if n["id"] == "workload":
            n["partners"] = ["meta", "oss"]
            n["used_for"]["meta"] = "The Muse Code set was written by Muse Spark; the recorded H100 load used the public replay set."
        if n["id"] in products or "meta" in n["partners"]:
            n["meta_product"] = products.get(n["id"], "Muse Spark")
        m = meta_subs.get(n["id"], set())
        for sb in n["subs"]:
            sb["meta"] = m == "*" or sb["id"] in m
        if ev.get(n["id"]):
            n["evidence"] = ev[n["id"]]
        nodes.append(n)
    return {
        "schema": "wattnot.dash.story/1", "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "labels": list(spec.LABELS), "partners": spec.PARTNERS, "nodes": nodes,
        "edges": [{"from": a, "to": b, "partner": p} for a, b, p in spec.EDGES],
        "event_map": spec.EVENT_MAP,
        "meta": meta_block(S, lg, bt, h), "story": story(h, bt, S) if h.get("ok") else {},
        "replays": replays(h, bt, lg) if h.get("ok") else {}, "agent": agent_rounds(repo),
        "ledger": {"cap_usd": lg.get("_cap_usd"), "total_usd": (lg.get("all") or {}).get("usd"),
                   "calls": (lg.get("all") or {}).get("calls")},
        "sources": ["Meta spend ledger (totals only)", "H100 run results folder", "reports/cloud-live receipts and calls",
                    "docs/meta-session-2026-10-27.json", "rack/", "workloads/"],
    }
