"""The eight steps of one WattNot run, and the room scan that opens it.

Room scan -> Baseline -> Quality gate -> Cap sweep (or Adaptive top-k) -> Approve -> Keep / rollback -> Receipt.

``nodes(variant, room)`` returns the graph nodes in the same shape the Architecture map uses, so the
run view and the map draw them with one component. ``PIPE_MAP`` says which node and sub-step a raw
``wattnot.event/1`` event lights when it does not carry ``data.node`` / ``data.sub`` itself (a live
stream from the runbook does not).

The room scan has a small JSON interface, ``wattnot.room/1``. A room module (a SAM-based one, or any
other) hands the run a scan in that shape; until then ``example_room`` supplies a clearly labelled
SIMULATED one. Nothing in this file segments an image.

    {"schema": "wattnot.room/1", "source": "stub" | "sam" | ..., "size": [w, h], "image": null | str,
     "racks": [{"id", "polygon": [[x, y], ...], "temp_c", "temp_label", "power_w", "power_label",
                "gpu": bool, "decision": "act" | "watch" | "skip", "reason"}]}

A label is one of MEASURED, ESTIMATE, SIMULATED. A missing number is ``null``, never a guess.
"""

from __future__ import annotations

import copy

ROOM_SCHEMA = "wattnot.room/1"
DECISIONS = ("act", "watch", "skip")
LABELS = ("MEASURED", "ESTIMATE", "SIMULATED")
SUPPLY_C = 22.0            # assumed cold-aisle supply temperature for the SIMULATED after-picture
HOT_C = 35.0               # a rack at or above this is a hotspot in the example room
TARGET_ID = "R-07"         # the rack that holds the real H100 in the example room

PUE = {"value": 1.56, "source": "Uptime Institute, Global Data Center Survey 2024: industry-average PUE 1.56 (526 respondents)",
       "url": "https://datacenter.uptimeinstitute.com/rs/711-RIA-145/images/2024.GlobalDataCenterSurvey.Report.pdf",
       "formula": "cooling and overhead energy = GPU energy x (PUE - 1)",
       "caveat": "An industry average, not this room's own PUE."}


def _sub(i, title, desc, code, inputs, outputs, *, meta=False, partner=None, event=None):
    return {"id": i, "title": title, "desc": desc, "code": code, "inputs": inputs, "outputs": outputs, "partner": partner, "event": event, "meta": meta}


_ROOM = [
    _sub("segment", "Segment the racks", "SAM 3.1 (Meta) outlines every rack in the room photo or thermal still.", "sam/rackmap.py", "a room photo or IR still", "one mask per rack", meta=True, partner="meta"),
    _sub("heat", "Read each rack's heat", "The thermal values inside each mask give a temperature per rack.", "sam/rackmap.py", "masks, thermal still", "temperature per rack", meta=True, partner="meta"),
    _sub("xcheck", "Cross-check with GPU power", "Where WattNot can read a card's power, it is set beside the rack's heat.", "meter/nvml.py", "rack ids, NVML power", "power per rack we can read", partner="nvidia", event="telemetry.sample"),
    _sub("rank", "Rank the hotspots", "Racks are ordered by temperature; a rack with no card access can only be watched.", "sam/pick.py", "temperature, power, access", "a ranked list", meta=True, partner="meta"),
    _sub("pick", "Muse Spark picks targets", "Says in plain words which racks to start with and why.", "planner/spark.py", "the ranked list", "target racks and a reason", meta=True, partner="meta", event="decision"),
]
_BASE_COMMON = [
    _sub("setup", "Set up the host", "The model server and the measuring tools go on the machine.", "runbook.py", "a VM, the kit script", "a ready host", partner="e2e"),
    _sub("grant", "Write the grant", "A short file says what may change, how far and for how long.", "approvals/service.py", "allowed knobs and bounds", "a proposal", event="proposal.created"),
    _sub("approve", "Approve it", "A person approves the grant before anything on the card changes.", "approvals/service.py", "the proposal", "approved"),
    _sub("probe", "Probe the power meter", "A short load confirms the card's own meter is reading before any number is trusted.", "meter/nvml.py", "a load window", "meter confirmed", partner="nvidia", event="telemetry.sample"),
    _sub("card", "Find the card", "NVML names the card and its allowed power range.", "meter/nvml.py", "NVML query", "card, cap range", partner="nvidia"),
]
VARIANTS = {
    "cap": {
        "baseline": _BASE_COMMON + [
            _sub("serve", "Serve the model at stock power", "The model runs on the card at its stock limit.", "runner/vllm_server.py", "model weights", "a running server", partner="e2e"),
            _sub("send", "Send the prompts", "The same prompts go to the server many at a time.", "runner/workload.py", "prompt set", "answers, timings"),
            _sub("measure", "Measure at stock power", "Energy per request and p99 at the stock cap: the line everything is compared with.", "runner/trial.py", "load, meter", "J per request, p99", partner="nvidia", event="trial.measured")],
        "gate": [_sub("limits", "Your limits become thresholds", "The latency and quality limits you set turn into numbers a gate can check.", "gates/decide.py", "your limits", "p99 limit, answer limit"),
                 _sub("check", "Answer check at stock power", "The answer gate is run on the stock answers first, so the gate itself can be trusted.", "gates/decide.py", "stock answers", "pass rate", event="gate.result")],
        "sweep": [_sub("pick", "Choose the caps to try", "Which power caps to try, and in what order.", "planner/spark.py", "history, allowed range", "a list of caps", meta=True, partner="meta", event="trial.proposed"),
                  _sub("cap", "Set a cap, measure, judge", "One cap: set it, run the load, read J per request and p99, check the gates.", "runbook.py", "a cap in watts", "J per request, p99, gate pass", partner="nvidia", event="trial.measured")],
    },
    "topk": {
        "baseline": _BASE_COMMON + [
            _sub("serve", "Load the model with normal top-k", "The model is loaded with its routers hooked but not yet changed.", "moe/adaptive_topk.py", "model weights", "a model with hooked routers", partner="hf"),
            _sub("measure", "Measure normal top-k", "Energy per token and p99 with the normal 8 experts per token.", "moe/adaptive_topk.py", "load, meter", "J per token, p99", partner="nvidia", event="trial.measured")],
        "gate": [_sub("limits", "Your limits become thresholds", "The latency and quality limits you set turn into numbers a gate can check.", "gates/decide.py", "your limits", "p99 limit, agreement limit"),
                 _sub("check", "Reference answers", "Normal top-k's own choices are the reference that agreement is measured against.", "moe/adaptive_topk.py", "normal top-k choices", "a reference")],
        "topk": [_sub("hook", "Keep fewer experts", "Each token keeps experts only until 90% of its router weight is covered.", "moe/adaptive_topk.py", "router weights", "experts kept per token", partner="hf"),
                 _sub("arms", "Run both versions", "Normal and adaptive run the same rounds; energy and experts used are measured.", "moe/adaptive_topk.py", "rounds per arm", "J per token, experts used", partner="nvidia", event="trial.measured"),
                 _sub("agree", "Agreement with normal top-k", "Every token is compared with what normal top-k would say.", "moe/adaptive_topk.py", "next-token choices", "agreement %", event="gate.result"),
                 _sub("p99", "Latency check", "The slowest batch against your limit.", "gates/decide.py", "batch times", "p99 change", event="gate.result")],
    },
    "hosted": {
        "baseline": [_sub("load", "Load the prompts", "Fifteen prompts in five classes.", "cloud/tune.py", "workload file", "15 prompts"),
                     _sub("send", "Send to the hosted model", "Every request goes to the model on Baseten.", "cloud/transport.py", "prompts", "answers, token usage", partner="baseten"),
                     _sub("measure", "Top level first", "Every prompt at the top reasoning level: the reference for cost and p99.", "cloud/tune.py", "top level", "reference cost, answers, p99", partner="baseten"),
                     _sub("usage", "Read the token usage", "Each reply reports its tokens and cached tokens; the cost comes from those.", "cloud/transport.py", "reply usage", "usage per request", partner="baseten")],
        "sweep": [_sub("pick", "Muse Spark proposes the order", "Which reasoning levels to try per class, and in what order.", "cloud/tune.py", "classes, allowed levels", "an order per class", meta=True, partner="meta", event="decision"),
                  _sub("try", "Try a cheaper level", "One class at one level: cost, quality and p99.", "cloud/tune.py", "class, level", "cost, quality, p99", partner="baseten"),
                  _sub("cache", "Shared text first", "Cache-friendly prompt order, A/B against changing text first.", "cloud/cache.py", "two prompt orders", "input cost saved", partner="baseten"),
                  _sub("warm", "Is the cache still warm", "A repeat after ten minutes.", "cloud/cache.py", "a repeat", "% from cache", partner="baseten")],
        "gate": [_sub("limits", "Your limits become thresholds", "The latency and quality limits you set.", "gates/decide.py", "your limits", "thresholds"),
                 _sub("judge", "A judge compares answers", "Another model compares each cheaper answer with the top-level one.", "cloud/tune.py", "answer pairs", "how many are worse", event="gate.result"),
                 _sub("p99", "Latency check", "p99 per class against your limit.", "gates/decide.py", "p99 per class", "p99 change", event="gate.result")],
    },
}
_APPROVE = [
    _sub("classify", "Rate the risk", "A small cap tweak near stock is low risk; anything bigger waits for a person.", "approvals/risk.py::classify", "the change, stock and TDP", "low or high, with reasons"),
    _sub("decide", "Operator check", "A low-risk change goes through once the gates pass; a big one waits for an operator to click Approve.", "approvals/guard.py::Guard.wait", "the proposal", "approved, rejected or expired"),
    _sub("undo", "One-click undo", "An applied change is put back with the token saved when it was applied.", "approvals/guard.py::Guard.undo", "the applied change", "stock restored")]
_KEEP = [_sub("decide", "Decide what to keep", "Everything that passed all gates is kept; the rest is not.", "gates/decide.py", "all trials, your limits", "a decision", event="decision"),
         _sub("apply", "Apply it", "The setting is applied to the card or the router.", "approvals/appliers.py", "the decision", "the change applied", partner="nvidia", event="action.applied"),
         _sub("rollback", "Roll back", "Nothing qualified, or the run ended: everything goes back to stock.", "approvals/appliers.py", "the previous setting", "stock restored", event="action.rolled_back")]
_RECEIPT = {
    "h100": [_sub("build", "Before and after, in numbers", "Energy, p99 and quality before and after, in one table.", "receipt/build.py", "all measurements", "the receipt body"),
             _sub("heat", "Room heat map, before and after", "The room picture again, with the racks that were acted on.", "sam/rackmap.py", "room scan, results", "before/after heat map", meta=True, partner="meta"),
             _sub("sign", "Sign it", "The receipt is signed with an offline key. Nothing goes to a public log.", "receipt/sign.py", "the receipt", "a signed receipt", partner="sigstore", event="receipt.signed")],
    "hosted": [_sub("build", "Before and after, in numbers", "Cost, p99 and quality before and after.", "receipt/build.py", "all measurements", "the receipt body"),
               _sub("verify", "Re-check every call", "Every recorded call is checked against the receipt.", "cloud/receipt.py", "calls log", "all matched", partner="baseten"),
               _sub("meter", "Billing meter", "Baseten's own billing meter, if the key allows it.", "cloud/transport.py", "billing API", "a bill to compare"),
               _sub("sign", "Write the receipt", "The result is written to a receipt.", "receipt/build.py", "classes, costs", "a receipt", partner="sigstore", event="receipt.signed")],
}
_META = {
    "room": ("Room scan", "Finds the racks in a room photo or thermal still, reads their heat, checks it against GPU power and picks where to start.", ["meta", "nvidia"], "SAM 3.1", "sam/rackmap.py"),
    "baseline": ("Baseline", "The workload at stock power: energy per request and p99, before anything changes.", ["e2e", "nvidia"], None, "runbook.py"),
    "gate": ("Quality gate", "Turns your limits into checks and proves the answer check works on the stock answers.", ["oss"], None, "gates/decide.py"),
    "sweep": ("Cap sweep", "Tries one setting at a time and measures J per request, p99 and whether the gates pass.", ["nvidia", "meta"], "Muse Spark", "runbook.py"),
    "topk": ("Adaptive top-k", "Mixture-of-Experts models keep fewer experts per token; agreement with normal top-k is the quality check.", ["hf", "nvidia"], None, "moe/adaptive_topk.py"),
    "approve": ("Approve", "Low-risk changes go through after the gates pass. A big one waits for an operator to click Approve. No answer in time means reject.", ["oss"], None, "approvals/guard.py"),
    "keep": ("Keep or roll back", "Keeps what passed every gate, rolls back what did not.", ["nvidia"], None, "approvals/appliers.py"),
    "receipt": ("Receipt", "Writes the result down: numbers, labels, and the room before and after.", ["sigstore"], None, "receipt/build.py"),
}
_ORDER = {"cap": ["room", "baseline", "gate", "sweep", "approve", "keep", "receipt"], "topk": ["room", "baseline", "gate", "topk", "approve", "keep", "receipt"],
          "hosted": ["baseline", "sweep", "gate", "approve", "keep", "receipt"]}
_TITLES = {("hosted", "sweep"): ("Effort sweep", "Tries cheaper reasoning levels per prompt class and a cache-friendly order."),
           ("hosted", "baseline"): ("Baseline", "The prompts at the top reasoning level: reference cost and p99.")}

# raw event type -> (node, sub). Used when an event has no data.node / data.sub (a live runbook stream).
PIPE_MAP = {"run.started": ["baseline", "setup"], "proposal.created": ["baseline", "grant"], "telemetry.sample": ["baseline", "probe"],
            "trial.proposed": ["sweep", "pick"], "decision": ["keep", "decide"], "trial.measured": ["sweep", "cap"],
            "gate.result": ["gate", "check"], "action.applied": ["keep", "apply"], "action.rolled_back": ["keep", "rollback"],
            "receipt.signed": ["receipt", "sign"]}

STAGES = tuple(_META)


def node_ids(variant: str, room: bool) -> list[str]:
    return [i for i in _ORDER[variant] if room or i != "room"]


def nodes(variant: str, room: bool, partners: dict | None = None) -> list[dict]:
    """Graph nodes for one run variant ('cap', 'topk' or 'hosted')."""
    out = []
    for col, nid in enumerate(node_ids(variant, room)):
        title, desc, parts, prod, code = _META[nid]
        title, desc = _TITLES.get((variant, nid), (title, desc))
        if nid == "room":
            subs = _ROOM
        elif nid == "approve":
            subs = _APPROVE
        elif nid == "keep":
            subs = _KEEP
        elif nid == "receipt":
            subs = _RECEIPT["hosted" if variant == "hosted" else "h100"]
        else:
            subs = VARIANTS[variant][nid]
        used = {p: f"{_META[nid][0]}: {desc}" for p in parts}
        out.append({"id": nid, "title": title, "partners": parts, "desc": desc, "code": code, "col": col, "row": 0, "subs": copy.deepcopy(subs),
                    "inputs": subs[0]["inputs"], "outputs": subs[-1]["outputs"], "used_for": used, "status": "code", "kind": "step", "lane": "Run",
                    "stats": [], "meta_product": prod})
    return out


def full_nodes() -> list[dict]:
    """Every stage with the sub-steps of every variant merged: what a live stream can light."""
    by: dict[str, dict] = {}
    for v in ("cap", "topk", "hosted"):
        for n in nodes(v, True):
            if n["id"] not in by:
                by[n["id"]] = n
            else:
                have = {x["id"] for x in by[n["id"]]["subs"]}
                by[n["id"]]["subs"] += [x for x in n["subs"] if x["id"] not in have]
    out = [by[i] for i in STAGES if i in by]
    for c, n in enumerate(out):
        n["col"] = c
    return out


# ------------------------------------------------------------------ the room scan
_EXAMPLE = [  # (temp_c, example power in watts, has a card WattNot can read)
    (27, 3400, False), (29, 4100, False), (31, 4600, False), (28, 3900, False),
    (30, 4200, False), (43, 9800, False), (41, None, True), (33, 5200, False),
    (26, 3100, False), (28, 3600, False), (32, 4900, False), (29, 3800, False)]


def example_room(gpu_w: float | None = None) -> dict:
    """A SIMULATED 12-rack room. One rack holds the real H100; its power is the one MEASURED number."""
    racks = []
    for i, (t, p, gpu) in enumerate(_EXAMPLE):
        col, row = i % 4, i // 4
        x, y, w, h = 14 + col * 92, 20 + row * 74, 78, 62
        rid = f"R-{i + 1:02d}"
        if gpu:
            dec, why = "act", "Hot, and WattNot can read and cap the H100 in this rack."
        elif t >= HOT_C:
            dec, why = "watch", "Hottest rack in the room, but WattNot has no card access here (example rack), so it can only be watched."
        else:
            dec, why = "skip", "Below the hotspot line."
        racks.append({"id": rid, "polygon": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], "temp_c": float(t), "temp_label": "SIMULATED",
                      "power_w": gpu_w if gpu else float(p), "power_label": ("MEASURED" if gpu_w is not None else None) if gpu else "SIMULATED",
                      "gpu": gpu, "decision": dec, "reason": why})
    return {"schema": ROOM_SCHEMA, "source": "stub", "label": "SIMULATED", "size": [400, 240], "image": None, "racks": racks,
            "note": "Example room. The temperatures and the other racks' power are made up for the demonstration; only the H100 rack's GPU power is a recorded measurement."}


def validate_room_scan(o) -> dict:
    """Check a scan from a room module. Raises ValueError with a plain message."""
    if not isinstance(o, dict) or o.get("schema") != ROOM_SCHEMA:
        raise ValueError(f"room scan must be an object with schema {ROOM_SCHEMA}")
    racks = o.get("racks")
    if not isinstance(racks, list) or not 1 <= len(racks) <= 200:
        raise ValueError("room scan needs 1 to 200 racks")
    size = o.get("size") or [400, 240]
    if not (isinstance(size, list) and len(size) == 2 and all(isinstance(v, (int, float)) and 0 < v <= 10000 for v in size)):
        raise ValueError("room scan size must be [width, height]")
    seen, out = set(), []
    for r in racks:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not r["id"] or len(r["id"]) > 40 or r["id"] in seen:
            raise ValueError("every rack needs a unique short string id")
        seen.add(r["id"])
        poly = r.get("polygon")
        if not (isinstance(poly, list) and 3 <= len(poly) <= 400 and all(isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) for v in p) for p in poly)):
            raise ValueError(f"rack {r['id']}: polygon must be 3 or more [x, y] points")
        for k in ("temp_c", "power_w"):
            if r.get(k) is not None and not isinstance(r[k], (int, float)):
                raise ValueError(f"rack {r['id']}: {k} must be a number or null")
        for k in ("temp_label", "power_label"):
            if r.get(k) is not None and r[k] not in LABELS:
                raise ValueError(f"rack {r['id']}: {k} must be one of {', '.join(LABELS)} or null")
        if r.get("decision") not in DECISIONS:
            raise ValueError(f"rack {r['id']}: decision must be one of {', '.join(DECISIONS)}")
        out.append({"id": r["id"], "polygon": [[float(a), float(b)] for a, b in poly], "temp_c": r.get("temp_c"), "temp_label": r.get("temp_label"),
                    "power_w": r.get("power_w"), "power_label": r.get("power_label"), "gpu": bool(r.get("gpu")), "decision": r["decision"],
                    "reason": str(r.get("reason") or "")[:300]})
    return {"schema": ROOM_SCHEMA, "source": str(o.get("source") or "provided")[:40], "label": o.get("label") if o.get("label") in LABELS else None,
            "size": [float(size[0]), float(size[1])], "image": None, "racks": out, "note": str(o.get("note") or "")[:300]}


def target_of(scan: dict) -> dict | None:
    acts = [r for r in scan["racks"] if r["decision"] == "act"]
    return max(acts, key=lambda r: r["temp_c"] if r["temp_c"] is not None else -1) if acts else None


def ranking(scan: dict) -> list[dict]:
    return sorted((r for r in scan["racks"] if r["temp_c"] is not None), key=lambda r: -r["temp_c"])


def after_room(scan: dict, target: dict | None, p_before: float | None, p_after: float | None) -> dict:
    """The same room after the run. Only the acted rack changes, and only when its power change is known.

    SIMULATED: the rack's rise above the supply temperature is assumed to fall in proportion to its power.
    """
    out = copy.deepcopy(scan)
    out["label"] = "SIMULATED"
    if target and p_before and p_after is not None and target["temp_c"] is not None:
        for r in out["racks"]:
            if r["id"] == target["id"]:
                r["temp_c"] = round(SUPPLY_C + (target["temp_c"] - SUPPLY_C) * (min(p_after, p_before) / p_before), 1)
                r["temp_label"] = "SIMULATED"
                r["power_w"] = round(p_after, 1)
    return out
