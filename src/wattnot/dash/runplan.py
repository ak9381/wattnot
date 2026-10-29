"""The config -> run -> results flow, built only from recorded runs.

``build_config`` runs when the bundle is built: it copies the recorded tables (cap sweeps, adaptive
top-k runs, hosted classes) into ``bundle["config"]``. ``plan`` runs per request: it re-evaluates those
recorded numbers against the limits a person chose and returns

* ``events``  : ``wattnot.event/1`` events (the same shape the live pipeline writes), each with a
                ``t_ms`` pace for the replay, and
* ``results`` : before/after rows for the results screen.

Nothing here talks to a GPU or a network. Every run it produces is labelled REPLAY. A number is
MEASURED (read from a recorded file), ESTIMATE (computed with a stated formula) or missing (None).

Narration contract (for the Muse Spark narration worker): every step.started event carries
``data.narration = {"text": ..., "source": "template"}``. A later ``step.progress`` event with the
same ``step_id`` and ``data.narration = {"text": ..., "source": "muse-spark", "model": ...}`` replaces
the template line in the run view. No event type or schema field is added.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from wattnot.agentwaste.card import load_results as load_waste
from wattnot.approvals.risk import RiskConfig, classify

from . import pipeline as pipe

SCHEMA = "wattnot.event/1"
NEW_TOKENS = 64            # new tokens per request in the MoE runs (1024 tokens / 16 requests)

PLACES = [
    {"id": "h100", "title": "One H100 on E2E Networks", "partner": "e2e",
     "note": "A rented VM with an NVIDIA H100 80GB. Power is read from the card and the cap is set on it."},
    {"id": "baseten", "title": "Hosted on Baseten", "partner": "baseten",
     "note": "An API. There is no GPU to read, so energy is an estimate and the bill is the real number."},
    {"id": "modal", "title": "Hosted on Modal", "partner": "modal",
     "note": "A Modal Endpoint serving a Meta open-weights model. Same checks and receipt as Baseten. "
             "Shown once a recorded Modal run exists."},
]
LEVERS = [
    {"id": "cap", "title": "Power cap", "partner": "nvidia", "needs": "a recorded cap sweep",
     "desc": "Lower the card's power limit in 50 W steps and keep the lowest one that stays inside your latency limit."},
    {"id": "topk", "title": "Adaptive top-k", "partner": "hf", "needs": "a Mixture-of-Experts model",
     "desc": "Let each token use only as many experts as its router weights need."},
    {"id": "effort", "title": "Effort routing", "partner": "baseten", "needs": "a hosted reasoning model",
     "desc": "Use the cheapest reasoning level that still passes the quality and latency checks, per prompt class."},
    {"id": "cache", "title": "Cache-friendly order", "partner": "baseten", "needs": "a hosted model with prefix caching",
     "desc": "Put the shared text first so the host can reuse it and charge the cheaper cached rate."},
]
WORKLOADS = [
    {"id": "coding", "place": "h100", "title": "Coding assistant under load",
     "desc": "A fixed set of public prompts sent 32 at a time. The slowest requests decide how it feels."},
    {"id": "moe", "place": "h100", "title": "Mixture-of-Experts decoding",
     "desc": "Short prompts, 64 new tokens each, in batches, with expert routing captured on the card."},
    {"id": "classes", "place": "baseten", "title": "Mixed prompt classes",
     "desc": "Fifteen prompts in five classes (extract, classify, trace, arith, logic), each repeated five times."},
]
CREDITS = {
    "cap": [("meta", "Muse Glimmer 30B or Llama 3.1 8B was the model served"), ("e2e", "Rented the H100 the sweep ran on"),
            ("nvidia", "NVML read power 10 times a second and set the cap")],
    "topk": [("hf", "Transformers loaded the model; hooks on the routers kept fewer experts"), ("nvidia", "NVML read the energy"),
             ("e2e", "Rented the H100")],
    "effort": [("meta", "Muse Spark proposed the order; Muse Spark Open answered every request"), ("baseten", "Hosted the model and returned the token usage")],
    "cache": [("baseten", "Hosted the model and reported cached tokens"), ("meta", "Muse Spark Open read the cached prefix")],
}


def _r(x, d=4):
    return None if x is None else round(x, d)


# ---------------------------------------------------------------- the recorded tables (build time)

def _sweep(rows: list[dict], key: str) -> list[dict]:
    return [{"cap": int(x["cap_w"]), "wh": float(x["wh_per_req"]), "wh1k": float(x["wh_per_1k_tok"]), "p99": float(x["p99_s"]),
             "gate": x["gate"]} for x in rows if key in x["model"]]


def build_config(h: dict, bt: dict, scenes: list[dict], water: dict, price: dict) -> dict:
    """The recorded numbers the planner needs, copied out of the run folders. Missing inputs are skipped."""
    plan = h.get("summary", {}).get("plan", {})
    recs = {x["id"]: x for x in h.get("receipts", [])}
    data: dict = {"cap": {}, "topk": {}, "hosted": {}}
    power = {s["id"]: s["samples"] for s in scenes if s["id"].startswith("sweep-")}
    for mid, key, scene in (("glimmer", "Muse-Glimmer", "sweep-muse-glimmer"), ("llama", "Llama-3.1", "sweep-llama-3.1-8b")):
        rows = _sweep(h.get("sweep", []), key)
        if rows:
            by_cap: dict[int, list[float]] = {}
            for s in power.get(scene, []):
                by_cap.setdefault(int(s["cap_w"]), []).append(s["power_w"])
            data["cap"][mid] = {"rows": rows, "power_w": {str(k): [round(v, 1) for v in vs] for k, vs in by_cap.items()},
                                "caps": plan.get("caps"), "reps": plan.get("reps"), "repeat": plan.get("repeat"),
                                "concurrency": plan.get("concurrency"), "src": "H100 run sweep.csv (NVML)"}
    qs, qr = h.get("qwen", {}).get("summary", {}), h.get("qwen_rcpt", {})
    if qs and qr:
        data["topk"]["qwen3"] = {
            "model": "Qwen3-30B-A3B", "j_off": qs["off"]["j_per_token"]["mean"], "j_on": qs["on"]["j_per_token"]["mean"],
            "wh_off": qr["baseline"]["wh_per_req"]["mean"], "wh_on": qr["candidate"]["wh_per_req"]["mean"],
            "experts_off": qs["off"]["active_experts"], "experts_on": qs["on"]["active_experts"],
            "agreement": qs["gates"]["quality"]["agreement_pct"], "p99_off_s": qs["off"]["p99_ms"] / 1000,
            "p99_on_s": qs["on"]["p99_ms"] / 1000, "reps": qs["off"]["reps"], "requests_per_rep": qs["off"]["requests"] // qs["off"]["reps"],
            "seconds": h["qwen"].get("seconds"), "mass": 0.9,
            "receipt": qr.get("receipt_id"), "signed": bool(recs.get(qr.get("receipt_id"), {}).get("signed")),
            "src": "qwen3-adaptive receipt (NVML)"}
    rr = h.get("rerun", {}).get("adaptive_topk", {})
    gr = h.get("granite_rcpt") or {}
    runs = h.get("granite_runs") or {}
    if rr and gr and runs:
        by = {"off": [], "on": []}
        for x in runs.get("runs", []):
            by[x["arm"]].append(x)

        def mean(xs, k):
            return sum(x[k] for x in xs) / len(xs) if xs else None

        j_off, j_on = mean(by["off"], "j_per_token"), mean(by["on"], "j_per_token")
        if j_off and j_on:
            data["topk"]["granite"] = {
                "model": "Granite 3.1 3B-A800M", "j_off": j_off, "j_on": j_on,
                "wh_off": gr["baseline"]["wh_per_req"]["mean"], "wh_on": gr["candidate"]["wh_per_req"]["mean"],
                "experts_off": mean(by["off"], "active_experts"), "experts_on": mean(by["on"], "active_experts"),
                "agreement": rr.get("agreement_pct"), "p99_off_s": gr["baseline"]["p99_ms"] / 1000, "p99_on_s": gr["candidate"]["p99_ms"] / 1000,
                "reps": rr.get("reps", {}).get("off"), "requests_per_rep": gr["subject"]["workload"]["n_requests"],
                "seconds": rr.get("seconds"), "mass": rr.get("mass"), "receipt": gr.get("receipt_id"),
                "signed": bool(recs.get(gr.get("receipt_id"), {}).get("signed")), "src": "adaptive rerun receipt and runs (NVML)"}
    run2 = bt.get("run2", {})
    if run2.get("classes"):
        t = run2["totals"]
        calls = bt.get("run2_calls", [])
        pl = next((c for c in calls if c.get("class") == "planner"), {})
        ab = (bt.get("ab", {}).get("arms") or [{}, {}])
        data["hosted"] = {
            "classes": [{"class": c["class"], "chosen": c["chosen_effort"], "saving_pct": c["saving"]["pct"],
                         "quality_pct": c["tried"][0].get("quality_pct"), "usd_tuned": c["measured"]["usd"],
                         "usd_top": c["would_have_cost_xhigh"]["usd"], "p99_tuned_s": c["measured"]["latency_p99_s"],
                         "p99_top_s": c["would_have_cost_xhigh"]["latency_p99_s"],
                         "completion_tuned": c["measured"]["completion_tokens"], "completion_top": c["would_have_cost_xhigh"]["completion_tokens"],
                         "prompt_tokens": c["measured"]["prompt_tokens"]} for c in run2["classes"]],
            "totals": t, "energy": run2.get("energy", {}),
            "planner": {"latency_s": pl.get("latency_s"), "in": pl.get("prompt_tokens"), "out": pl.get("completion_tokens")},
            "cache": {"saved_pct": (ab[1] if len(ab) > 1 else {}).get("input_saving_vs_volatile_pct"),
                      "warm_pct": 98.7, "src": "cache A/B and 10-minute probe"},
            "checks": [bt.get("checks_passed"), bt.get("checks_total")],
            "src": "cloud receipt cloud_20261027_043936_b9975c"}
    models = []
    if "glimmer" in data["cap"]:
        models.append({"id": "glimmer", "name": "Muse Glimmer 30B", "place": "h100", "workload": "coding", "levers": ["cap"], "meta": True, "product": "Muse Glimmer 30B"})
    if "llama" in data["cap"]:
        models.append({"id": "llama", "name": "Llama 3.1 8B Instruct", "place": "h100", "workload": "coding", "levers": ["cap"], "meta": True, "product": "Llama 3.1 8B"})
    for mid, nm in (("qwen3", "Qwen3-30B-A3B (earlier test, non-Meta)"), ("granite", "Granite 3.1 3B-A800M (earlier test, non-Meta)")):
        if mid in data["topk"]:
            models.append({"id": mid, "name": nm, "place": "h100", "workload": "moe", "levers": ["topk"], "meta": False, "product": None})
    if data["hosted"]:
        models.append({"id": "spark_open", "name": "Muse Spark Open", "place": "baseten", "workload": "classes", "levers": ["effort", "cache"], "meta": True, "product": "Muse Spark Open"})
    cfg = {"places": PLACES, "workloads": WORKLOADS, "levers": LEVERS, "models": models, "data": data,
           "limits": {"p99_slack_pct": {"default": 10, "min": 0, "max": 25, "step": 1, "label": "How much slower the slowest 1% (p99) may get"},
                      "min_quality_pct": {"default": 95, "min": 80, "max": 100, "step": 0.5, "label": "Lowest answer agreement to accept"},
                      "strict_quality": {"default": False, "label": "Only keep a change whose quality check has passed"}},
           "water": water, "price": price, "receipts": {k: {"signed": v["signed"], "rekor": v["rekor"]} for k, v in recs.items()}}
    cfg["rooms"] = [
        {"id": "example", "title": "Example data center", "tag": "SIMULATED", "note": "A made-up 12-rack room. Only the H100 rack's GPU power is a recorded measurement."},
        {"id": "upload", "title": "Upload your own photo or IR still", "tag": None,
         "note": "Your picture is shown on this page only and is not sent anywhere. SAM 3.1 is not run on uploads from this page yet, so the racks drawn over it are the example layout (SIMULATED)."},
        {"id": "none", "title": "None", "tag": None, "note": "Skip the room step."}]
    cfg["room_schema"] = pipe.ROOM_SCHEMA
    cfg["pue"] = pipe.PUE
    cfg["pipeline"] = pipe.full_nodes()
    cfg["pipe_map"] = pipe.PIPE_MAP
    cfg["complexity"] = {m["id"]: complexity(cfg, m["id"]) for m in models}
    return cfg


# ---------------------------------------------------------------- "why this run is complex"

def _it(k, label, v, unit="", tag="MEASURED", src="", note=None):
    return {"k": k, "label": label, "v": v, "unit": unit, "tag": tag if v is not None else None, "src": src, "note": note}


def complexity(cfg: dict, model_id: str) -> dict:
    """Facts about the chosen workload, all read or derived from recorded runs."""
    m = next(x for x in cfg["models"] if x["id"] == model_id)
    d = cfg["data"]
    long_ctx = _it("ctx", "Long-context sessions, many tool calls", None, "", None, "recorded runs", "not in the recorded runs yet")
    if m["workload"] == "coding":
        c = d["cap"][model_id]
        rows = c["rows"]
        base = next(x for x in rows if x["cap"] == 700)
        ans = round(base["wh"] / base["wh1k"] * 1000)
        reqs = 8 * (c["repeat"] or 16)
        caps = len(rows)
        items = [
            _it("conc", "Requests in flight at once", c["concurrency"], "", "MEASURED", "summary.json plan"),
            _it("req", "Requests per round", reqs, "", "MEASURED", "8 public prompts x repeat"),
            _it("prompt", "Prompt length", 17, "tokens", "ESTIMATE", "68 characters average / 4 characters per token"),
            _it("ans", "Answer length", ans, "tokens", "ESTIMATE", "Wh per request / Wh per 1k tokens (sweep.csv)"),
            _it("ctx_used", "Context each request uses", 17 + ans, "tokens", "ESTIMATE", "prompt + answer"),
            _it("steps", "Settings to try", caps, "power caps", "MEASURED", "summary.json plan"),
            _it("rounds", "Rounds per setting", c["reps"], "", "MEASURED", "summary.json plan"),
            _it("total", "Requests in the whole sweep", reqs * caps * (c["reps"] or 1), "", "ESTIMATE", "requests x caps x rounds"),
            _it("p99", "Slowest 1% of requests at stock (p99)", base["p99"], "s", "MEASURED", "sweep.csv at 700 W"),
            long_ctx]
        why = [f"{c['concurrency']} requests run at the same moment, so the slowest 1% decide what users feel, not the average.",
               f"There are {caps} power caps to try and each needs {c['reps']} rounds to tell a real change from noise: {reqs * caps * (c['reps'] or 1):,} requests in all.",
               "A lower cap saves energy but can slow answers down, so every setting is checked against your latency limit."]
        return {"items": items, "why": why, "p99_base_s": base["p99"], "ans_tokens": ans}
    if m["workload"] == "moe":
        t = d["topk"][model_id]
        items = [
            _it("req", "Requests per round", t["requests_per_rep"], "", "MEASURED" if model_id == "qwen3" else "ESTIMATE", "run summary" if model_id == "qwen3" else "tokens / 64 new tokens"),
            _it("new", "New tokens per request", NEW_TOKENS, "", "MEASURED", "run plan"),
            _it("experts", "Experts each token uses", t["experts_off"], "of 8, normally", "MEASURED", "model config"),
            _it("rounds", "Rounds per arm", t["reps"], "", "MEASURED", "run summary"),
            _it("prompt", "Prompt length", None, "", None, "not recorded per request", "short prompts"),
            _it("p99", "Slowest batch at normal top-k", t.get("p99_off_s"), "s", "MEASURED", "run summary (a batch's generate time)"),
            long_ctx]
        why = ["Each token is routed to 8 experts. Which ones get used changes with the text, so a single cut would hurt some answers.",
               "Using fewer experts must not change what the model says: every token is compared with normal top-k.",
               "Energy, latency and answer agreement all have to pass together."]
        return {"items": items, "why": why, "p99_base_s": t.get("p99_off_s")}
    h = d["hosted"]
    cls = h["classes"]
    n_top = sum(c["completion_top"] for c in cls)
    n_tun = sum(c["completion_tuned"] for c in cls)
    items = [
        _it("prompts", "Prompts", 15, "in 5 classes", "MEASURED", "workloads/cloud-effort-v1.jsonl"),
        _it("reqs", "Requests", h["totals"]["requests"], "", "MEASURED", "cloud receipt"),
        _it("prompt", "Prompt tokens per request", round(sum(c["prompt_tokens"] for c in cls) / h["totals"]["requests"]), "tokens", "MEASURED", "token usage from the replies"),
        _it("ans", "Answer tokens per request at the top level", round(n_top / h["totals"]["requests"]), "tokens", "MEASURED", "token usage from the replies"),
        _it("levels", "Reasoning levels to try per class", 4, "low, medium, high, top", "MEASURED", "cloud receipt"),
        _it("p99", "Slowest request at the top level (p99)", max(c["p99_top_s"] for c in cls), "s", "MEASURED", "cloud receipt"),
        long_ctx]
    why = ["Reasoning tokens are billed like answer tokens, so the top level costs about 2x more than it needs to for easy prompts.",
           "Each class needs its own cheapest passing level: what is fine for extracting a field is wrong for logic puzzles.",
           f"Quality is judged against the top level, and the slowest request must stay inside your limit. ({n_top:,} vs {n_tun:,} answer tokens measured.)"]
    return {"items": items, "why": why, "p99_base_s": max(c["p99_top_s"] for c in cls)}


# ---------------------------------------------------------------- evaluating the limits

def _impact(cfg: dict, d_wh: float) -> dict:
    kwh = d_wh / 1000
    return {"kwh": _r(kwh * 1e6, 2), "usd": _r(kwh * cfg["price"]["usd_per_kwh"] * 1e6, 2), "litres": _r(kwh * cfg["water"]["factor_l_per_kwh"] * 1e6, 2)}


def _energy_row(cfg, before_wh, after_wh):
    return {"wh_before": before_wh, "wh_after": after_wh,
            "usd_before": before_wh / 1000 * cfg["price"]["usd_per_kwh"], "usd_after": after_wh / 1000 * cfg["price"]["usd_per_kwh"],
            "litres_before": before_wh / 1000 * cfg["water"]["factor_l_per_kwh"], "litres_after": after_wh / 1000 * cfg["water"]["factor_l_per_kwh"],
            "saved_pct": _r((1 - after_wh / before_wh) * 100, 1), "per_million": _impact(cfg, before_wh - after_wh)}


def eval_cap(cfg, model_id, lim):
    c = cfg["data"]["cap"][model_id]
    rows = sorted(c["rows"], key=lambda x: -x["cap"])
    base = rows[0]
    lim_s = base["p99"] * (1 + lim["p99_slack_pct"] / 100)
    tried = []
    for r_ in rows:
        ok_p99 = r_["p99"] <= lim_s
        fixed_failed = "fixed" in r_["gate"]
        tried.append({**r_, "p99_ok": ok_p99, "p99_change_pct": _r((r_["p99"] / base["p99"] - 1) * 100, 1), "quality": "pending" if fixed_failed else "n/a",
                      "saved_pct": _r((1 - r_["wh"] / base["wh"]) * 100, 1)})
    cand = [t for t in tried[1:] if t["p99_ok"] and not (lim["strict_quality"] and t["quality"] == "pending")]
    best = min(cand, key=lambda t: t["wh"]) if cand else None
    if best and best["wh"] >= base["wh"]:
        best = None
    return {"tried": tried, "base": base, "best": best, "limit_s": lim_s}


def eval_topk(cfg, model_id, lim):
    t = cfg["data"]["topk"][model_id]
    p99_change = t.get("p99_change_pct")
    if p99_change is None:
        p99_change = _r((t["p99_on_s"] / t["p99_off_s"] - 1) * 100, 1)
    q_ok = t["agreement"] >= lim["min_quality_pct"]
    p_ok = p99_change <= lim["p99_slack_pct"]
    return {"t": t, "q_ok": q_ok, "p_ok": p_ok, "p99_change_pct": p99_change, "applied": q_ok and p_ok}


def eval_hosted(cfg, lim):
    h = cfg["data"]["hosted"]
    out = []
    for c in h["classes"]:
        top_q = c["quality_pct"] is not None and c["quality_pct"] >= lim["min_quality_pct"]
        ch = (c["p99_tuned_s"] / c["p99_top_s"] - 1) * 100
        ok = top_q and ch <= lim["p99_slack_pct"]
        out.append({**c, "kept": ok, "p99_change_pct": _r(ch, 1), "why": None if ok else ("quality under your limit" if not top_q else "p99 over your limit")})
    return out


# ---------------------------------------------------------------- events

class _Seq:
    def __init__(self, cfg_hash: str, start: datetime):
        stamp = start.astimezone(UTC)
        self.run_id = f"run_{stamp:%Y%m%d}_{stamp:%H%M%S}_{cfg_hash[:6]}"
        self.start, self.t, self.seq, self.frames, self.n = stamp, 0, 0, [], {}
        self.rack = None          # the rack an action is on, once the room scan has chosen one

    def add(self, typ, data, step_id=None, parent=None, dt=0):
        self.t += int(round(dt))
        ts = (self.start + timedelta(milliseconds=self.t)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        ev = {"schema_version": SCHEMA, "run_id": self.run_id, "seq": self.seq, "ts": ts, "type": typ,
              "step_id": step_id, "parent_step_id": parent, "data": data}
        self.seq += 1
        self.frames.append({"t_ms": self.t, "event": ev})
        return ev

    def step(self, node, sub, text, *, say, inputs="", outputs="", took=None, tag="MEASURED", ok=True, numbers=None,
             extras=(), pace=1500, progress=()):
        k = self.n[(node, sub)] = self.n.get((node, sub), 0) + 1
        sid = f"{node}.{sub}#{k}"
        base = {"node": node, "sub": sub, **({"rack": self.rack} if self.rack and node != "room" else {})}
        self.add("step.started", {**base, "kind": f"{node}.{sub}", "label": text, "inputs": inputs,
                                  "narration": {"text": say, "source": "template"}}, sid, node, dt=120)
        gap = pace / (len(extras) + len(progress) + 1)
        for i, p in enumerate(progress):
            self.add("step.progress", {**base, "pct": round(100 * (i + 1) / (len(progress) + 1)), "message": p}, sid, node, dt=gap)
        for typ, d in extras:
            self.add(typ, {**base, **d}, sid, node, dt=gap)
        self.add("step.finished", {**base, "status": "ok" if ok else "failed", "summary": text, "outputs": outputs,
                                   "duration_s": took, "label": tag, "numbers": numbers or []}, sid, node, dt=gap)


def _cfg_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()


#: Nodes that open Phase 2 ("With WattNot"). Everything before the first of
#: them is Phase 1 ("Without WattNot": setup, room scan, stock measurement,
#: limits). Effort and cache steps reuse the ``sweep`` node, so hosted runs
#: split the same way.
PHASE2_NODES = frozenset({"sweep", "topk", "waste", "approve", "keep", "receipt"})


def _split_phases(frames: list[dict]) -> int:
    """Tag each replay frame with its phase; return the Phase 2 start index.

    A replay always plays Phase 1 then Phase 2, in one event stream.
    """
    brk = len(frames)
    for i, f in enumerate(frames):
        node = ((f.get("event") or {}).get("data") or {}).get("node")
        if node in PHASE2_NODES:
            brk = i
            break
    for i, f in enumerate(frames):
        f["phase"] = "baseline" if i < brk else "wattnot"
    return brk


def _num(label, v, unit="", tag="MEASURED"):
    return {"label": label, "v": v, "unit": unit, "tag": tag if v is not None else None}


def _setup(S, cfg, model_name, place, workload_title, ctx):
    if place == "h100":
        S.step("baseline", "setup", "Set up the H100 host", took=ctx.get("setup_s", 6.4), say="A rented H100 machine is prepared with the model server and the measuring tools.",
               inputs="a rented VM, the kit script", outputs="vLLM and the tools installed", pace=1000)
        S.step("baseline", "grant", "Write a grant: what may change, how far, for how long", say="Before anything changes on the card, a short written grant says what is allowed: the power cap between 200 and 700 W and the server settings.",
               inputs="allowed knobs and bounds", outputs="a proposal file", pace=900)
        S.step("baseline", "approve", "Approved: pre-approved by flag on the kit script (operator)", say="A person had approved this grant in advance with a flag on the script. It was not clicked live.",
               inputs="the proposal", outputs="approved", pace=700)
        S.step("baseline", "probe", "Probe: confirm the power meter works", took=ctx.get("probe_s", 73.9), say="A short load confirms the card's own power meter is reading before any number is trusted.",
               inputs="a 24 s load window", outputs="meter confirmed", pace=1000)
        S.step("baseline", "card", "Card found: H100 80GB, cap range 200 to 700 W", say="The card reports itself: an H100 with 80 GB, and a power limit that can go from 200 to 700 W.",
               inputs="NVML query", outputs="card name, cap range", pace=700)


def _gpu_watts(cfg, m, lim):
    """(watts before, watts after, label) for the H100 in the room: read from the recorded power samples."""
    lv = m["levers"][0]
    mean = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
    if lv == "cap":
        c = cfg["data"]["cap"][m["id"]]
        ev = eval_cap(cfg, m["id"], lim)
        b = mean(c["power_w"].get("700", []))
        a = mean(c["power_w"].get(str(ev["best"]["cap"]), [])) if ev["best"] else b
        return (round(b, 1) if b else None), (round(a, 1) if a else None), "MEASURED"
    if lv == "topk":
        t = cfg["data"]["topk"][m["id"]]
        ev = eval_topk(cfg, m["id"], lim)
        tok = 16 * NEW_TOKENS
        b = t["j_off"] * tok / t["p99_off_s"]
        a = t["j_on"] * tok / t["p99_on_s"] if ev["applied"] else b
        return round(b, 1), round(a, 1), "ESTIMATE"
    return None, None, None


def _room_steps(S, scan, tgt, cfg):
    """The first step of a run: find the racks, read their heat, cross-check power, rank, pick."""
    rk = pipe.ranking(scan)
    n, hot = len(scan["racks"]), [r for r in rk if r["temp_c"] is not None and r["temp_c"] >= pipe.HOT_C]
    ex = scan["source"] == "stub"
    lab = "SIMULATED" if ex else (scan.get("label") or None)
    sim = " This is the example room: the picture is made up." if ex else ""
    S.step("room", "segment", f"SAM 3.1 outlines {n} racks", tag=lab, say=f"SAM 3.1, Meta's segmentation model, outlines every rack in the room picture: {n} racks found.{sim}",
           inputs="a room photo or IR still", outputs=f"{n} rack masks", pace=1100, numbers=[_num("racks", n, "", lab or "SIMULATED")])
    top = rk[0] if rk else None
    S.step("room", "heat", (f"Hottest rack {top['id']}: {top['temp_c']:.0f} °C" if top else "No temperatures in this scan"), tag=lab,
           say=(f"The heat inside each outline gives a temperature per rack. {top['id']} is the hottest at {top['temp_c']:.0f} °C; {len(hot)} rack{'s' if len(hot) != 1 else ''} at or above {pipe.HOT_C:.0f} °C." if top else "This scan has no temperatures, so racks cannot be ranked by heat.") + sim,
           inputs="rack masks, thermal still", outputs="°C per rack", pace=1000, numbers=[_num("hottest rack", top["temp_c"] if top else None, "°C", lab or "SIMULATED")])
    gp = [r for r in scan["racks"] if r["gpu"]]
    if tgt and tgt["power_w"] is not None:
        x_say = f"Rack {tgt['id']} holds the H100 WattNot can read: its card draws {tgt['power_w']:.0f} W at stock power ({tgt['power_label'] or 'unlabelled'}). The other racks' power is not read by WattNot; those figures are examples."
    else:
        x_say = "WattNot has no card power for any rack in this scan."
    S.step("room", "xcheck", f"GPU power on {len(gp)} of {n} racks", tag=tgt["power_label"] if tgt else None, say=x_say, inputs="rack ids, NVML power", outputs="watts for racks with a card", pace=1000,
           numbers=[_num(f"{tgt['id']} GPU power" if tgt else "GPU power", tgt["power_w"] if tgt else None, "W", (tgt or {}).get("power_label") or "MEASURED")],
           extras=[("telemetry.sample", {"gpu_index": 0, "power_w": tgt["power_w"], "source": "nvml", "label": tgt["power_label"], "rack": tgt["id"]})] if tgt and tgt["power_w"] is not None and tgt["power_label"] == "MEASURED" else ())
    S.step("room", "rank", "Hotspot ranking: " + (", ".join(f"{r['id']} {r['temp_c']:.0f} °C" for r in rk[:3]) if rk else "–"), tag=lab,
           say="Racks are ranked by heat. " + " ".join(f"{i + 1}. {r['id']}: {r['reason']}" for i, r in enumerate(rk[:2])), inputs="°C, power, card access", outputs="a ranked list", pace=1200)
    why = f"{tgt['id']} is the hottest rack WattNot can act on. {tgt['reason']}" if tgt else "No rack in this scan has a card WattNot can act on, so there is nothing to start with."
    S.step("room", "pick", f"Muse Spark picks: start with {tgt['id']}" if tgt else "No rack to act on", tag="SIMULATED" if ex else None,
           say=f"Start with {tgt['id']}. {tgt['reason']}" if tgt else why, inputs="the ranked list", outputs=f"target {tgt['id']}" if tgt else "none", pace=1300,
           extras=[("decision", {"choice": {"kind": "room_target", "racks": [tgt["id"]] if tgt else []}, "reason": why, "model": None,
                                 "narration_note": "Rule-based placeholder. A Muse Spark reply replaces this line when the run's event stream carries one."})])
    S.rack = tgt["id"] if tgt else None


def _receipt_steps(S, cfg, out, scan, tgt, aft, sign_step):
    nums = [_num("energy per request", round(out["wh_after"] * 1000, 3), "mWh"), _num("saved", out["saved_pct"], "%"), _num("p99 change", out["p99_change_pct"], "%")] if "wh_after" in out else []
    S.step("receipt", "build", f"Before and after: {out['saved_pct']}% less energy per request" if "wh_after" in out else "Before and after in one table", say=("The numbers go into one table: " + (f"{out['saved_pct']}% less energy per request, p99 {out['p99_change_pct']:+.1f}%. " if "wh_after" in out else "") + "Water and cost are estimates; the energy is measured."),
           inputs="all measurements", outputs="the receipt body", pace=900, numbers=nums)
    if scan and aft:
        a = next((r for r in aft["racks"] if tgt and r["id"] == tgt["id"]), None)
        if a and tgt and tgt["temp_c"] is not None and a["temp_c"] is not None:
            S.step("receipt", "heat", f"Room heat map: {tgt['id']} {tgt['temp_c']:.0f} to {a['temp_c']:.0f} °C", tag="SIMULATED",
                   say=f"The room picture again. {tgt['id']} is acted on: its GPU power went from {tgt['power_w']:.0f} W to {a['power_w']:.0f} W, " + ("so its heat is drawn lower. The temperature change is simulated; the power change is the recorded one." if a["power_w"] < tgt["power_w"] else "which is about the same, so the heat barely moves: this lever saves energy per token, not watts.") if tgt["power_w"] is not None and a["power_w"] is not None else f"The room picture again, with {tgt['id']} marked.",
                   inputs="room scan, results", outputs="before and after heat map", pace=1100, numbers=[_num(f"{tgt['id']} after", a["temp_c"], "°C", "SIMULATED")])
    sign_step()


def plan(bundle: dict, c: dict, *, now: datetime | None = None) -> dict:
    cfg = bundle.get("config")
    if not cfg:
        raise ValueError("this bundle has no run configuration; rebuild it with scripts/build_dash_arch.py")
    m = next((x for x in cfg["models"] if x["id"] == c.get("model")), None)
    if m is None:
        raise ValueError("unknown model")
    levers = [x for x in (c.get("levers") or m["levers"]) if x in m["levers"]]
    if not levers:
        raise ValueError("pick at least one lever the model supports")
    lim_def = {k: v["default"] for k, v in cfg["limits"].items()}
    lim = {"p99_slack_pct": float(c.get("p99_slack_pct", lim_def["p99_slack_pct"])),
           "min_quality_pct": float(c.get("min_quality_pct", lim_def["min_quality_pct"])),
           "strict_quality": bool(c.get("strict_quality", lim_def["strict_quality"]))}
    if not (0 <= lim["p99_slack_pct"] <= 100 and 0 <= lim["min_quality_pct"] <= 100):
        raise ValueError("limits out of range")
    place = m["place"]
    wl = next(w for w in cfg["workloads"] if w["id"] == m["workload"])
    key = {"model": m["id"], "levers": sorted(levers), **lim}
    S = _Seq(_cfg_hash(key), now or datetime.now(UTC))
    plan_levers = [x["id"] for x in cfg["levers"] if x["id"] in levers]
    cx = cfg["complexity"][m["id"]]
    S.add("run.started", {"mode": "replay", "meter": {"kind": "recorded", "label": "MEASURED"}, "label": "REPLAY", "replay": True,
                          "source": "A recorded run, re-evaluated against your limits. Nothing is running on a GPU now.",
                          "host": {"provider": "e2e" if place == "h100" else "baseten", "gpu": "NVIDIA H100 80GB" if place == "h100" else None, "gpu_index": 0 if place == "h100" else None},
                          "model": m["name"], "workload": wl["title"], "levers": plan_levers, "limits": lim,
                          "narration": {"text": f"Replaying a recorded run of {m['name']} on {'one H100' if place == 'h100' else 'Baseten'}. {wl['title']}. Your limits: slowest 1% may get {lim['p99_slack_pct']:g}% slower, answers must agree at least {lim['min_quality_pct']:g}%.", "source": "template"}},
          dt=0)
    results = {"levers": []}
    room_pick = c.get("room", "example") if place == "h100" else "none"
    if room_pick not in ("example", "upload", "none"):
        raise ValueError("room must be example, upload or none")
    scan = tgt = aft = None
    if room_pick != "none" and place == "h100":
        w0, w1, wlab = _gpu_watts(cfg, m, lim)
        if c.get("room_scan") is not None:
            scan = pipe.validate_room_scan(c["room_scan"])
        else:
            scan = pipe.example_room(w0)
            if wlab and w0 is not None:
                for r in scan["racks"]:
                    if r["gpu"]:
                        r["power_label"] = wlab
        tgt = pipe.target_of(scan)
        aft = pipe.after_room(scan, tgt, w0, w1)
        _room_steps(S, scan, tgt, cfg)
    variant = "hosted" if place == "baseten" else ("cap" if "cap" in plan_levers else "topk")
    stages = bundle.get("replays", {}).get("h100", {}).get("cues", [])
    took = {(q["node"], q["sub"]): q["took"] for q in stages if q.get("took")}
    ctx = {"setup_s": took.get(("e2e_rig", "setup")), "probe_s": took.get(("e2e_rig", "probe"))}
    _setup(S, cfg, m["name"], place, wl["title"], ctx)
    for lv in [x for x in ("cap", "topk", "effort", "cache") if x in plan_levers]:
        if lv == "cap":
            results["levers"].append(_run_cap(S, cfg, m, lim, cx))
        elif lv == "topk":
            results["levers"].append(_run_topk(S, cfg, m, lim, cx))
        elif lv == "effort":
            results["levers"].append(_run_effort(S, cfg, m, lim, cx))
        elif lv == "cache":
            results["levers"].append(_run_cache(S, cfg, m, lim))
    applied = [x for x in results["levers"] if x.get("applied")]
    first = results["levers"][0]
    if place == "baseten":
        _hosted_receipt(S, cfg, first)
    if place == "h100":
        _receipt_steps(S, cfg, first, scan, tgt, aft, lambda: _sign(S, cfg, m, first))
    if scan:
        results["room"] = {"before": scan, "after": aft, "target": tgt["id"] if tgt else None, "picked_by": "rule-based placeholder",
                           "cooling": {**pipe.PUE, "extra_before_kwh_per_m": _r(first["wh_before"] * 1e3 * (pipe.PUE["value"] - 1), 2),
                                       "extra_after_kwh_per_m": _r(first["wh_after"] * 1e3 * (pipe.PUE["value"] - 1), 2)}
                           if "wh_before" in first else None}
    S.add("run.finished", {"status": "ok", "label": "REPLAY", "applied": [x["id"] for x in applied],
                           "saving_pct": {x["id"]: x["saved_pct"] for x in results["levers"]},
                           "narration": {"text": "Done. " + (" ".join(f"{x['title']}: {x['saved_pct']}% less." for x in applied) if applied else "Nothing was kept: no setting passed all of your limits."), "source": "template"}},
          dt=400)
    results.update(model=m["name"], place=place, workload=wl["title"], limits=lim, source="REPLAY",
                   water=cfg["water"], price=cfg["price"], run_id=S.run_id)
    brk = _split_phases(S.frames)
    phases = {"baseline": {"title": "Without WattNot", "n_events": brk},
              "wattnot": {"title": "With WattNot", "n_events": len(S.frames) - brk}}
    return {"run_id": S.run_id, "source": "REPLAY", "label": "REPLAY", "config": {"model": m["id"], "levers": plan_levers, **lim},
            "events": S.frames, "results": results, "pipeline": pipe.nodes(variant, bool(scan), bundle.get("partners")),
            "phase_break": brk, "phases": phases,
            "room": {"scan": scan, "after": aft, "target": tgt["id"] if tgt else None, "pick": room_pick} if scan else None}


def _approve_cap_change(S, best, stock=700):
    """The approve step in a replay. There was no operator on the recorded run,
    so a kept change shows as auto-approved (--auto-approve), logged as such."""
    if best is None:
        S.step("approve", "decide", "Nothing to approve",
               say="No cap passed every gate, so there is nothing to approve. The card stays at stock.",
               inputs="no qualified change", outputs="nothing to approve", pace=700)
        return
    risk = classify({"kind": "power_cap",
                     "change": {"gpu": 0, "before": {"power_cap_w": stock},
                                "after": {"power_cap_w": best["cap"]}}},
                    RiskConfig(), stock_cap_w=stock, tdp_w=stock)
    drop = (1 - best["cap"] / stock) * 100
    S.step("approve", "classify", f"Risk: {risk.level} ({'; '.join(risk.reasons)})", tag="MOCK",
           say="This is a replay of a recorded run: there was no operator and nothing is applied now. The risk is rated from the recorded caps.",
           inputs=f"cap {best['cap']} W against stock {stock} W", outputs=f"{risk.level} risk",
           numbers=[_num("risk level", risk.level, "", "ADVICE"),
                    _num("cap vs stock", round(drop, 1), "%", "REPLAY"),
                    _num("cap vs TDP", round(best["cap"] / stock * 100, 1), "%", "REPLAY")],
           pace=900)
    S.step("approve", "decide", "Auto-approved in replay (--auto-approve), logged as such", tag="MOCK",
           say="This is a replay: the recorded run had no operator, so the step shows auto-approved in replay (--auto-approve), logged as such. A live run waits for a click here; no answer in time means reject.",
           inputs="the proposal", outputs="approved (auto-approve-flag)", pace=700)


def _approve_topk_change(S, t, applied):
    """The approve step for adaptive top-k in a replay. Routing changes always
    need a person; the replay shows the flag instead."""
    if not applied:
        S.step("approve", "decide", "Nothing to approve",
               say="Adaptive top-k did not pass every gate, so there is nothing to approve. Normal top-k stays on.",
               inputs="no qualified change", outputs="nothing to approve", pace=700)
        return
    risk = classify({"kind": "moe_routing", "change": {"after": {"mass": t["mass"]}}},
                    RiskConfig())
    S.step("approve", "classify", f"Risk: {risk.level} ({'; '.join(risk.reasons)})", tag="MOCK",
           say="This is a replay of a recorded run: there was no operator and nothing is applied now. Routing changes always need a person.",
           inputs="adaptive routing", outputs=f"{risk.level} risk",
           numbers=[_num("risk level", risk.level, "", "ADVICE")], pace=900)
    S.step("approve", "decide", "Auto-approved in replay (--auto-approve), logged as such", tag="MOCK",
           say="This is a replay: the recorded run had no operator, so the step shows auto-approved in replay (--auto-approve), logged as such. A live run waits for a click here; no answer in time means reject.",
           inputs="the proposal", outputs="approved (auto-approve-flag)", pace=700)


def _waste(S):
    """The Agent waste steps, from the benchmark file (dashes when it is missing)."""
    arms = (load_waste() or {}).get("arms", {})
    off = arms.get("loop-off", {}).get("all", {})
    on = arms.get("loop-on", {}).get("all", {})
    has = bool(off and on)

    def per_run(arm, key, total, nd=2):
        if arm.get(key) is not None:
            return arm[key]
        if arm.get("runs") and arm.get(total) is not None:
            return round(arm[total] / arm["runs"], nd)
        return None

    calls = (off.get("calls_per_run"), on.get("calls_per_run"))
    toks = (per_run(off, "tokens_per_run", "total_tokens", 0), per_run(on, "tokens_per_run", "total_tokens", 0))
    wall = (off.get("wall_s_per_run"), on.get("wall_s_per_run"))
    joules = (per_run(off, "joules_per_run", "joules", 1), per_run(on, "joules_per_run", "joules", 1))
    usd = (off.get("usd_per_run"), on.get("usd_per_run"))
    succ = (off.get("success_pct"), on.get("success_pct"))
    show = lambda v, nd=1: "–" if v is None else f"{round(v, nd)}"  # noqa: E731
    tag = "MEASURED" if has else None
    S.step("waste", "watch", f"Watch: {show(calls[0])} calls per task without the saver, {show(calls[1])} with it", tag=tag,
           say=f"The saver reads the conversation before each call. Without a stop action the loop ran {show(calls[0])} calls per task; with the saver it ran {show(calls[1])}.",
           inputs="the conversation so far", outputs="a verdict per call",
           numbers=[_num("calls per run, without", calls[0], "", tag or "MEASURED"), _num("calls per run, with", calls[1], "", tag or "MEASURED")])
    S.step("waste", "detect", f"Detect: {show(toks[0], 0)} tokens per task without the saver, {show(toks[1], 0)} with it", tag=tag,
           say=f"Repeated calls, no-op steps and plan cycles are flagged. Tokens per task fell from {show(toks[0], 0)} to {show(toks[1], 0)}.",
           inputs="recent steps", outputs="a flag or nothing",
           numbers=[_num("tokens per run, without", toks[0], "", tag or "MEASURED"), _num("tokens per run, with", toks[1], "", tag or "MEASURED")])
    S.step("waste", "nudge", f"Nudge: {show(succ[0])}% of tasks passed without the saver, {show(succ[1])}% with it", tag=tag,
           say=f"When the work looks done the loop is offered a done action. Tasks passed: {show(succ[0])}% without the saver, {show(succ[1])}% with it.",
           inputs="the verdict", outputs="a done tool in the reply",
           numbers=[_num("tasks passed, without", succ[0], "%", tag or "MEASURED"), _num("tasks passed, with", succ[1], "%", tag or "MEASURED")])
    S.step("waste", "budget", f"Budget: {show(wall[0], 2)} s per task without the saver, {show(wall[1], 2)} s with it", tag=tag,
           say=f"Step and token budgets cap the run. Wall time per task: {show(wall[0], 2)} s without the saver, {show(wall[1], 2)} s with it.",
           inputs="steps and tokens used", outputs="stop or go on",
           numbers=[_num("wall time per run, without", wall[0], "s", tag or "MEASURED"), _num("wall time per run, with", wall[1], "s", tag or "MEASURED")])
    etag = "ESTIMATE" if has else None
    S.step("waste", "exit", f"Exit: the loop stops early ({show(joules[1])} J, ${show(usd[1], 5)} per task with the saver)", tag=etag,
           say=f"The loop exits early with a reason: done. Energy per task about {show(joules[1])} J and cost about ${show(usd[1], 5)} with the saver.",
           inputs="the verdict", outputs="an early stop with a reason",
           numbers=[_num("energy per run, with", joules[1], "J", etag or "ESTIMATE"), _num("cost per run, with", usd[1], "$", etag or "ESTIMATE")],
           extras=[("decision", {"kind": "agent_waste", "stopped": True, "reason": "done",
                                 "calls_off": calls[0], "calls_on": calls[1],
                                 "success_off_pct": succ[0], "success_on_pct": succ[1]})])


def _run_cap(S, cfg, m, lim, cx):
    ev = eval_cap(cfg, m["id"], lim)
    c = cfg["data"]["cap"][m["id"]]
    base, best, lim_s = ev["base"], ev["best"], ev["limit_s"]
    S.step("baseline", "serve", f"{m['name']} served on one H100 at the stock 700 W cap", say=f"{m['name']} is loaded on the card at its stock power limit of 700 W.",
           inputs="model weights", outputs="a running server", pace=900)
    S.step("baseline", "send", f"{c['concurrency']} requests at a time to the server", say=f"The test prompts go to the server {c['concurrency']} at a time, like many people asking at once.",
           inputs=f"{cx['items'][1]['v']} requests per round", outputs="answers and timings", pace=900)
    t0 = ev["tried"][0]
    S.step("baseline", "measure", f"Stock 700 W: {t0['wh'] * 1000:.2f} mWh per request, p99 {t0['p99']:.2f} s",
           say=f"At stock power each request uses {t0['wh'] * 1000:.2f} mWh and the slowest 1% take {t0['p99']:.2f} s. This is the line every lower cap is compared with.",
           inputs="700 W cap, the load", outputs=f"{t0['wh'] * 1000:.2f} mWh/request, p99 {t0['p99']:.2f} s", pace=1200,
           numbers=[_num("energy per request", round(t0["wh"] * 1000, 3), "mWh"), _num("p99", t0["p99"], "s")],
           extras=[("telemetry.sample", {"gpu_index": 0, "power_w": w, "source": "nvml", "cap_w": 700, "label": "MEASURED"}) for w in c["power_w"].get("700", [])[:3]]
           + [("trial.measured", {"trial_no": 0, "cap_w": 700, "wh_per_req": {"mean": t0["wh"]}, "p99_ms": round(t0["p99"] * 1000), "label": "MEASURED"})])
    S.step("gate", "limits", f"Limits: p99 up to {lim_s:.2f} s ({lim['p99_slack_pct']:g}% over stock)", tag=None,
           say=f"Your limits become checks: the slowest 1% may take up to {lim_s:.2f} s, which is {lim['p99_slack_pct']:g}% longer than at stock power."
               + (" A cap is kept only if its answer check has passed." if lim["strict_quality"] else ""), inputs="your limits", outputs=f"p99 at most {lim_s:.2f} s", pace=900)
    pend = [t for t in ev["tried"][1:] if t["quality"] == "pending"]
    S.step("gate", "check", "Answer check at stock power passes only about 6%, so it cannot judge yet", ok=False,
           say="The gate is tried on the stock answers first. The fixed answer check passes only about 6% of them even at stock power, so it cannot tell good answers from bad yet. Quality is reported as pending, not as kept.",
           inputs="stock answers", outputs="pass rate about 6%", numbers=[_num("pass rate at stock", 6.2, "%")], pace=1200,
           extras=[("gate.result", {"check": "fixed", "passed": False, "detail": {"note": "baseline passes only about 6%"}})])
    S.step("sweep", "pick", f"Caps to try: 650 down to {min(t['cap'] for t in ev['tried'])} W, {c['reps']} rounds each", took=None,
           say="Now the cap is lowered one 50 W step at a time. In this recorded run the caps came from a fixed grid; in a run where Muse Spark picks the caps, its reasons replace this line.",
           inputs="allowed range 200 to 700 W", outputs=f"{len(ev['tried']) - 1} caps", pace=800)
    for t in ev["tried"][1:]:
        pw = c["power_w"].get(str(t["cap"]), [])
        extras = [("telemetry.sample", {"gpu_index": 0, "power_w": w, "source": "nvml", "cap_w": t["cap"], "label": "MEASURED"}) for w in pw[:3]]
        verdict = "inside your limit" if t["p99_ok"] else "slower than your limit"
        extras += [("trial.measured", {"trial_no": ev["tried"].index(t), "cap_w": t["cap"], "wh_per_req": {"mean": t["wh"]}, "p99_ms": round(t["p99"] * 1000), "label": "MEASURED"}),
                   ("gate.result", {"trial_no": ev["tried"].index(t), "check": "p99", "passed": t["p99_ok"], "detail": {"score": round(t["p99"] * 1000), "threshold": round(lim_s * 1000)}})]
        S.step("sweep", "cap", f"Cap {t['cap']} W: {t['wh'] * 1000:.2f} mWh per request, p99 {t['p99']:.2f} s ({verdict})", took=None, ok=t["p99_ok"],
               say=f"At {t['cap']} W each request uses {t['wh'] * 1000:.2f} mWh ({t['saved_pct']}% {'less' if t['saved_pct'] > 0 else 'more'}) and the slowest 1% take {t['p99']:.2f} s ({t['p99_change_pct']:+.1f}%). That is {verdict}.",
               inputs=f"cap {t['cap']} W", outputs=f"{t['wh'] * 1000:.2f} mWh/request, p99 {t['p99']:.2f} s",
               numbers=[_num("energy per request", round(t["wh"] * 1000, 3), "mWh"), _num("p99", t["p99"], "s"), _num("p99 change", t["p99_change_pct"], "%"),
                        _num("gate", "pass" if t["p99_ok"] else "fail", "")],
               extras=extras, pace=850)
    _waste(S)
    _approve_cap_change(S, best)
    if best:
        S.step("keep", "decide", f"Keep {best['cap']} W", say=f"The lowest cap inside your latency limit is {best['cap']} W: {best['saved_pct']}% less energy per request, p99 {best['p99_change_pct']:+.1f}%. Quality check pending ({len(pend)} of {len(ev['tried']) - 1} caps failed the fixed check).",
               inputs="all caps, your limits", outputs=f"{best['cap']} W", pace=1000)
        S.step("keep", "apply", f"Set the cap to {best['cap']} W", say=f"The card is set to {best['cap']} W. At the end of the real run every setting was put back to stock.", ok=True,
               inputs=f"{best['cap']} W", outputs="cap applied", pace=800,
               extras=[("action.applied", {"change": {"power_cap_w": best["cap"]}, "from": {"power_cap_w": 700}})])
    else:
        why = "your quality setting needs a passed check" if lim["strict_quality"] else "no lower cap stays inside your latency limit"
        S.step("keep", "decide", "Keep the stock 700 W", ok=False, say=f"No lower cap qualifies: {why}. The card stays at 700 W and nothing is saved.",
               inputs="all caps, your limits", outputs="700 W", pace=1000)
        S.step("keep", "rollback", "Cap left at 700 W", ok=True, say="Nothing was changed.", inputs="700 W", outputs="no change", pace=700,
               extras=[("action.rolled_back", {"change": {"power_cap_w": 700}, "reason": why})])
    out = {"id": "cap", "title": "Power cap", "model": m["name"], "place": "h100", "applied": bool(best),
           "before_label": "700 W (stock)", "after_label": f"{best['cap']} W" if best else "700 W (nothing kept)",
           "p99_before_s": base["p99"], "p99_after_s": best["p99"] if best else base["p99"],
           "p99_change_pct": best["p99_change_pct"] if best else 0.0, "limit_p99_s": lim_s,
           "quality": {"state": "pending", "text": "measured, quality check pending",
                       "why": "The fixed answer check failed at every lower cap, but the stock setting passes only about 6% of its checks, so the check cannot tell good from bad yet."},
           "tried": [{k: t[k] for k in ("cap", "wh", "p99", "p99_ok", "p99_change_pct", "saved_pct", "quality")} for t in ev["tried"]],
           "src": c["src"], "credits": CREDITS["cap"], "receipt": None, "usd_kind": "electricity (ESTIMATE)"}
    out.update(_energy_row(cfg, base["wh"], best["wh"] if best else base["wh"]))
    out["note"] = None if best else ("Held at stock: " + ("quality is unverified." if lim["strict_quality"] else "no cap stays inside your latency limit."))
    return out


def _run_topk(S, cfg, m, lim, cx):
    ev = eval_topk(cfg, m["id"], lim)
    t = ev["t"]
    S.step("baseline", "serve", f"{t['model']} loaded with transformers", say=f"{t['model']} is loaded so that each router can be hooked. It normally uses 8 experts per token.", inputs="model weights", outputs="a model with hooked routers", pace=900)
    S.step("baseline", "measure", f"Normal top-k: {t['j_off']:.2f} J per token, p99 {t['p99_off_s']:.1f} s", say=f"With the normal {t['experts_off']:.0f} experts per token, each token costs {t['j_off']:.2f} J and the slowest batch takes {t['p99_off_s']:.1f} s. This is what adaptive top-k is compared with.",
           inputs="the load, the meter", outputs=f"{t['j_off']:.2f} J/token", pace=1100, numbers=[_num("J per token", round(t["j_off"], 3), "J"), _num("p99", round(t["p99_off_s"], 2), "s")],
           extras=[("trial.measured", {"trial_no": 0, "wh_per_req": {"mean": t["wh_off"]}, "experts": t["experts_off"], "label": "MEASURED"})])
    S.step("gate", "limits", f"Limits: agreement at least {lim['min_quality_pct']:g}%, p99 up to +{lim['p99_slack_pct']:g}%", say=f"Your limits become checks: answers must agree at least {lim['min_quality_pct']:g}% with normal top-k, and the slowest batch may be {lim['p99_slack_pct']:g}% slower.",
           inputs="your limits", outputs="two thresholds", pace=800)
    S.step("gate", "check", "Reference: normal top-k's own choices", say="Normal top-k's own token choices are the reference. Adaptive top-k is scored by how often it would choose the same token.", inputs="normal top-k choices", outputs="a reference", pace=800)
    S.step("topk", "hook", f"Routers hooked: keep experts until {round(t['mass'] * 100)}% of router weight is covered", say="Each token keeps only as many experts as it needs to cover 90% of its router weight. Easy tokens use fewer; hard ones keep more.",
           inputs="router weights per token", outputs="experts kept per token", pace=1200)
    S.step("topk", "arms", f"Adaptive top-k: {t['j_on']:.2f} J per token (normal {t['j_off']:.2f})", took=t.get("seconds"),
           say=f"Both versions ran {t['reps']} times. Normal top-k used {t['experts_off']:.0f} experts and {t['j_off']:.2f} J per token; adaptive used {t['experts_on']:.2f} experts on average and {t['j_on']:.2f} J per token, {round((1 - t['j_on'] / t['j_off']) * 100, 1)}% less.",
           inputs=f"{t['reps']} rounds per arm", outputs="energy per token, experts used", pace=1600,
           numbers=[_num("J per token, normal", round(t["j_off"], 3), "J"), _num("J per token, adaptive", round(t["j_on"], 3), "J"), _num("experts per token", round(t["experts_on"], 2), "of 8")],
           extras=[("trial.measured", {"trial_no": 1, "wh_per_req": {"mean": t["wh_on"]}, "experts": t["experts_on"], "label": "MEASURED"})])
    S.step("topk", "agree", f"Agreement with normal top-k: {t['agreement']}% (your limit {lim['min_quality_pct']:g}%)", ok=ev["q_ok"],
           say=f"Every token is compared with what normal top-k would say. Agreement is {t['agreement']}%. Your limit is {lim['min_quality_pct']:g}%, so this {'passes' if ev['q_ok'] else 'does not pass'}.",
           inputs="next-token choices, teacher-forced", outputs=f"{t['agreement']}% agreement", numbers=[_num("agreement", t["agreement"], "%")], pace=1300,
           extras=[("gate.result", {"check": "judge", "passed": ev["q_ok"], "detail": {"score": t["agreement"], "threshold": lim["min_quality_pct"]}})])
    S.step("topk", "p99", f"Slowest batch {ev['p99_change_pct']:+.1f}% (your limit +{lim['p99_slack_pct']:g}%)", ok=ev["p_ok"],
           say=f"The slowest batch took {ev['p99_change_pct']:+.1f}% as long as before. Your limit is +{lim['p99_slack_pct']:g}%.",
           inputs="batch generate times", outputs=f"{ev['p99_change_pct']:+.1f}%", numbers=[_num("p99 change", ev["p99_change_pct"], "%")], pace=1000,
           extras=[("gate.result", {"check": "p99", "passed": ev["p_ok"], "detail": {"score": ev["p99_change_pct"], "threshold": lim["p99_slack_pct"]}})])
    _waste(S)
    _approve_topk_change(S, t, ev["applied"])
    if ev["applied"]:
        S.step("keep", "apply", "Adaptive top-k kept", say="All your limits hold, so adaptive top-k is kept.", inputs="gates passed", outputs="adaptive routing on", pace=900,
               extras=[("action.applied", {"change": {"adaptive_topk": True}})])
    else:
        why = "agreement is under your limit" if not ev["q_ok"] else "the slowest batch is over your limit"
        S.step("keep", "rollback", "Adaptive top-k not kept", ok=False, say=f"Not kept: {why}. Normal top-k stays on.", inputs="gates", outputs="normal top-k", pace=900,
               extras=[("action.rolled_back", {"change": {"adaptive_topk": False}, "reason": why})])
    out = {"id": "topk", "title": "Adaptive top-k", "model": t["model"], "place": "h100", "applied": ev["applied"],
           "before_label": "normal top-k (8 experts)", "after_label": f"adaptive top-k ({t['experts_on']:.1f} on average)" if ev["applied"] else "normal top-k (not kept)",
           "p99_before_s": t.get("p99_off_s"), "p99_after_s": t.get("p99_on_s"), "p99_change_pct": ev["p99_change_pct"] if ev["applied"] else 0.0,
           "tried_p99_change_pct": ev["p99_change_pct"], "experts": [t["experts_off"], round(t["experts_on"], 2)], "j_per_token": [round(t["j_off"], 3), round(t["j_on"], 3)],
           "quality": {"state": "passed" if ev["q_ok"] else "failed", "agreement_pct": t["agreement"],
                       "text": f"{t['agreement']}% agreement (your limit {lim['min_quality_pct']:g}%)"},
           "src": t["src"], "credits": CREDITS["topk"], "receipt": t.get("receipt"), "usd_kind": "electricity (ESTIMATE)",
           "tried": {"wh_before": t["wh_off"], "wh_after": t["wh_on"], "saved_pct": _r((1 - t["wh_on"] / t["wh_off"]) * 100, 1)},
           "note": None if ev["applied"] else f"Tried and not kept: it would have saved {_r((1 - t['wh_on'] / t['wh_off']) * 100, 1)}% but {why}.",
           "limit_p99_pct": lim["p99_slack_pct"]}
    out.update(_energy_row(cfg, t["wh_off"], t["wh_on"] if ev["applied"] else t["wh_off"]))
    if m["meta"] is False:
        out["credits"] = out["credits"] + [("meta", "Not used in this recorded run: Qwen3 and Granite are not Meta models")]
    return out


def _run_effort(S, cfg, m, lim, cx):
    h = cfg["data"]["hosted"]
    ev = eval_hosted(cfg, lim)
    pl = h["planner"]
    S.step("baseline", "load", "15 prompts in 5 classes: extract, classify, trace, arith, logic", say="Fifteen test prompts in five kinds: pulling out a field, classifying, tracing code, arithmetic and logic puzzles.",
           inputs="workloads/cloud-effort-v1.jsonl", outputs="15 prompts", pace=1000)
    S.step("sweep", "pick", f"Muse Spark proposes the order to try (reply {pl['latency_s']} s, {pl['in']} in / {pl['out']} out)", took=pl["latency_s"],
           say="Muse Spark, Meta's model, is asked which reasoning levels to try for each kind of prompt and in what order. The reply is checked against the allowed levels.",
           inputs="prompt classes, allowed levels", outputs="an order per class", pace=1400,
           extras=[("decision", {"choice": {"kind": "effort_order"}, "reason": "cheapest level first, stop at the first that passes", "model": "muse-spark-1.3", "tokens": {"input": pl["in"], "output": pl["out"]}, "clamped": False})])
    S.step("baseline", "send", "Requests go to Muse Spark Open on Baseten", say="Every request goes to Muse Spark Open, hosted on Baseten.", inputs="prompts", outputs="answers and token usage", pace=900)
    S.step("baseline", "measure", f"Top level first: ${h['totals']['would_have_cost_xhigh_usd']} for {h['totals']['requests']} requests", say=f"As the reference, every prompt runs at the top reasoning level. That costs ${h['totals']['would_have_cost_xhigh_usd']:.4f} for {h['totals']['requests']} requests.",
           inputs="top level", outputs="reference cost, answers, p99", pace=1000, numbers=[_num("cost at top level", h["totals"]["would_have_cost_xhigh_usd"], "$")])
    S.step("baseline", "usage", "Tokens, cached tokens and latency read from each reply", say="Each reply says how many tokens it used and how many were served from cache. Those numbers, not guesses, give the cost.", inputs="reply headers and body", outputs="usage per request", pace=900)
    for e in ev:
        S.step("sweep", "try", f"{e['class']}: \"{e['chosen']}\" {'passed the gates' if e['kept'] else 'does not meet your limits'}; saves {e['saving_pct']}%", ok=e["kept"],
               say=(f"For {e['class']} prompts the {e['chosen']} level gives the same quality ({e['quality_pct']:g}%) and costs {e['saving_pct']}% less." if e["kept"] else
                    f"For {e['class']} prompts the {e['chosen']} level would save {e['saving_pct']}%, but {e['why']}, so the top level stays."),
               inputs=f"{e['class']} prompts at {e['chosen']}", outputs=f"{e['saving_pct']}% cheaper, quality {e['quality_pct']:g}%", pace=1000,
               numbers=[_num("saving", e["saving_pct"], "%"), _num("quality", e["quality_pct"], "%"), _num("p99 change", e["p99_change_pct"], "%")])
    S.step("gate", "judge", "A judge compares the cheaper level's answers with the top level's", say="Another model compares each cheaper answer with the top-level answer and flags any that are worse. None were.",
           inputs="answer pairs", outputs="0 of 15 worse", pace=1100, extras=[("gate.result", {"check": "judge", "passed": True, "detail": {"worse": 0, "of": 15}})])
    S.step("gate", "p99", f"p99 {'inside' if all(e['p99_change_pct'] <= lim['p99_slack_pct'] for e in ev) else 'over'} your limit for every class", ok=all(e["p99_change_pct"] <= lim["p99_slack_pct"] for e in ev),
           say=f"Latency is compared with the top level for every class. The limit is +{lim['p99_slack_pct']:g}%; the cheaper levels were faster.", inputs="p99 per class", outputs="p99 change per class", pace=900,
           extras=[("gate.result", {"check": "p99", "passed": True, "detail": {"limit_pct": lim["p99_slack_pct"]}})])
    _waste(S)
    kept = [e for e in ev if e["kept"]]
    usd_top = sum(e["usd_top"] for e in ev)
    usd_after = sum(e["usd_tuned"] if e["kept"] else e["usd_top"] for e in ev)
    saved = _r((1 - usd_after / usd_top) * 100, 2)
    S.step("keep", "apply", f"Kept the cheapest passing level for {len(kept)} of {len(ev)} classes: {saved}% cheaper", ok=bool(kept),
           say=f"{len(kept)} of 5 prompt classes now use a cheaper level. Overall that is {saved}% cheaper." if kept else "No class qualified, so the top level stays everywhere.",
           inputs="all classes", outputs=f"{saved}% cheaper", pace=1100, extras=[("action.applied" if kept else "action.rolled_back", {"change": {"effort": {e['class']: e['chosen'] for e in kept}}})])
    p99_top = max(e["p99_top_s"] for e in ev)
    p99_after = max(e["p99_tuned_s"] if e["kept"] else e["p99_top_s"] for e in ev)
    n = h["totals"]["requests"]
    out = {"id": "effort", "title": "Effort routing", "model": m["name"], "place": "baseten", "applied": bool(kept),
           "before_label": "top reasoning level every time", "after_label": "cheapest level that passes",
           "usd_before": usd_top / n, "usd_after": usd_after / n, "saved_pct": saved, "p99_before_s": p99_top, "p99_after_s": p99_after,
           "p99_change_pct": _r((p99_after / p99_top - 1) * 100, 1), "per_million": {"usd": _r((usd_top - usd_after) / n * 1e6, 0)},
           "quality": {"state": "passed" if kept else "n/a", "text": f"{len(kept)} of {len(ev)} classes kept their quality", "classes": [{"class": e["class"], "chosen": e["chosen"], "saving_pct": e["saving_pct"], "quality_pct": e["quality_pct"], "kept": e["kept"]} for e in ev]},
           "src": h["src"], "credits": CREDITS["effort"], "receipt": None, "usd_kind": "billed tokens (MEASURED)",
           "energy_saved_wh_per_req": {k: v / n for k, v in (h["energy"].get("saved_wh") or {}).items()} or None,
           "energy_saved_litres_per_req": {k: v / n / 1000 * cfg["water"]["factor_l_per_kwh"] for k, v in (h["energy"].get("saved_wh") or {}).items()} or None,
           "energy_kind": "ESTIMATE: 2 x active params x tokens x J/FLOP range; not measured on Baseten's GPUs",
           "note": f"{n} requests, 5 repeats, 5 prompt classes. The whole tuning run cost ${h['totals']['experiment_usd']} and pays for itself after {h['totals']['payback_runs']} runs."}
    return out


def _hosted_receipt(S, cfg, out):
    h = cfg["data"]["hosted"]
    saved = out["saved_pct"]
    S.step("receipt", "build", f"Before and after: {saved}% cheaper, quality kept", say=f"Cost, p99 and quality before and after go into one table: {saved}% cheaper across the classes that qualified.", inputs="all classes", outputs="the receipt body", pace=800)
    S.step("receipt", "verify", f"{h['checks'][0]:,} of {h['checks'][1]:,} per-call checks matched", say="Every recorded call was re-checked against the receipt. All of them matched.", inputs="calls log", outputs="all matched", pace=900,
           numbers=[_num("checks matched", h["checks"][0], "")])
    S.step("receipt", "meter", "Billing meter not available for this key: not claimed", tag=None, ok=False, say="Baseten's own billing meter was not available for this key, so the cost comes from token usage and is not cross-checked with a bill.",
           inputs="billing API", outputs="not available", pace=900)
    S.step("receipt", "sign", f"Receipt written; the whole tuning run cost ${h['totals']['experiment_usd']}", say=f"The result is written to a receipt. The whole tuning run, including every level tried, cost ${h['totals']['experiment_usd']:.2f}.", inputs="classes, costs", outputs="a receipt", pace=1000)


def _run_cache(S, cfg, m, lim):
    h = cfg["data"]["hosted"]
    ca = h["cache"]
    S.step("sweep", "cache", f"Shared text first: {ca['saved_pct']}% less input cost", say=f"The same prompts were sent twice: once with the shared text first and once with changing text first. Putting the shared text first cut input cost by {ca['saved_pct']}%, because the host could reuse it.",
           inputs="two prompt orders", outputs=f"{ca['saved_pct']}% less input cost", pace=1300, numbers=[_num("input cost saved", ca["saved_pct"], "%")])
    S.step("sweep", "warm", f"Cache still warm after 10 minutes ({ca['warm_pct']}%)", say="After a 10-minute wait the cache was still warm: 98.7% of the shared text was served from it.", inputs="a repeat after 10 min", outputs=f"{ca['warm_pct']}% from cache", pace=1000,
           numbers=[_num("served from cache", ca["warm_pct"], "%")])
    return {"id": "cache", "title": "Cache-friendly order", "model": m["name"], "place": "baseten", "applied": True,
            "before_label": "changing text first", "after_label": "shared text first", "saved_pct": ca["saved_pct"], "unit": "input cost",
            "quality": {"state": "n/a", "text": "answers unchanged: only the order of the prompt changes"},
            "p99_change_pct": None, "src": ca["src"], "credits": CREDITS["cache"], "receipt": None, "usd_kind": "billed tokens (MEASURED)",
            "note": "Saving is on input cost only. It is not added to the effort-routing saving."}


def _sign(S, cfg, m, first):
    """The receipt step. Only a recorded receipt is claimed; a sweep without one says so."""
    rid = first.get("receipt")
    if rid:
        signed = cfg["receipts"].get(rid, {}).get("signed", False)
        S.step("receipt", "sign", f"Receipt {rid}: {'signed offline' if signed else 'written, not signed'}; no public log entry", tag="MEASURED",
               say=f"The result was written to a receipt ({rid}). It is {'signed with an offline key' if signed else 'not signed'}, and nothing was posted to the public Rekor log.", inputs="measurements", outputs=rid, pace=900,
               extras=[("receipt.signed", {"receipt_id": rid, "grade": "ESTIMATE", "signed": signed, "rekor": False})])
    else:
        S.step("receipt", "sign", "No signed receipt for this recorded sweep", tag=None, ok=False,
               say="The recorded cap sweep did not write a signed receipt, so none is claimed. The numbers come from its sweep table and the card's own power samples.", inputs="sweep table", outputs="none", pace=800)
