"""Options for the v2 Configure screen, with a plain-word explanation for each.

Every option maps to a real setting in the code (a CLI flag or a field the replay planner reads).
``applies`` says where it takes effect today:

* ``replay``  the recorded run is re-evaluated with it right now (runplan.plan reads it)
* ``live``    it is saved with the run config and used only by a live run on a GPU, which this
              screen never starts

Nothing here talks to a GPU or a network.
"""

from __future__ import annotations


def _o(id, label, choices, default, *, group, applies, flag, what, why, example):
    return {"id": id, "label": label, "group": group, "applies": applies, "flag": flag,
            "choices": [c if isinstance(c, dict) else {"v": c, "label": str(c)} for c in choices],
            "default": default, "help": {"what": what, "why": why, "example": example}}


def _c(v, label):
    return {"v": v, "label": label}


def options(cfg: dict) -> dict:
    """The full option list. ``cfg`` is the story bundle's ``config`` block (models, limits, rooms)."""
    models = [_c(m["id"], f"{m['name']} ({'one H100' if m.get('place') == 'h100' else 'Baseten'})")
              for m in cfg.get("models", [])]
    lim = cfg.get("limits", {})
    p99 = lim.get("p99_slack_pct", {}).get("default", 10)
    q = lim.get("min_quality_pct", {}).get("default", 95)
    rooms = [_c(r["id"], r["title"]) for r in cfg.get("rooms", [])]
    o = [
        _o("model", "Model", models, models[0]["v"] if models else None, group="basics", applies="replay",
           flag="--model / --adaptive-model",
           what="The AI model WattNot serves and tunes. Each one has a recorded run behind it.",
           why="Savings depend on the model. A big dense model reacts to a power cap differently from a Mixture-of-Experts model, which can also use top-k.",
           example="Muse Glimmer 30B on one H100: a lower power cap saved 21% energy per request in the recorded sweep."),
        _o("goal", "Goal",
           [_c("save", "Save power: accept a little slowdown"),
            _c("balanced", "Balanced: the default limits"),
            _c("fast", "Fastest: keep replies as quick as stock")],
           "balanced", group="basics", applies="replay", flag="goal (preset for --p99-budget-s and min_quality_pct)",
           what="What matters most for this run: saving power, a balance, or keeping replies quick.",
           why="Each goal sets the two gates to values already used in the recorded sweeps. Save power allows more slowdown, fastest allows almost none. You can still change each gate by hand under Advanced settings.",
           example="Save power allows the slowest 1% to rise 15% with 94% answer agreement; fastest allows 5% with 97% agreement."),
        _o("workload", "Workload (the prompts we send)",
           [_c("auto", "Match the model's recorded workload"), _c("coding", "Coding assistant under load"),
            _c("moe", "Mixture-of-Experts decoding"), _c("classes", "Mixed prompt classes (complex)")],
           "auto", group="basics", applies="live", flag="--workload",
           what="The set of prompts sent to the model during the test. 'Complex' here means mixed prompt classes: extraction, classification, tracing code, arithmetic and logic, each repeated five times. It is about the prompts, not the code.",
           why="A setting that saves power on easy prompts can break hard ones. Mixed, harder prompts make the quality check meaningful, because there is a correct answer to lose.",
           example="'Trace what this Python loop prints' has one right answer. If a lower setting changes it, the quality gate catches that."),
        _o("concurrency", "Requests in flight at once", [1, 8, 16, 32, 64], 32, group="basics", applies="live",
           flag="--concurrency",
           what="How many requests the model handles at the same time.",
           why="A busy GPU behaves differently from an idle one. The slowest 1% of requests (p99) gets worse first under load, so we test at a realistic load.",
           example="32 at a time is what the recorded Glimmer sweep used."),
        _o("profile", "Measurement length",
           [_c("quick", "Quick: 3 rounds x 2 repeats"), _c("standard", "Standard: 5 x 6, with control blocks"),
            _c("long", "Long: 7 x 12, with control blocks")],
           "standard", group="basics", applies="live", flag="--profile",
           what="How many times each setting is measured before we trust the number.",
           why="GPU power is noisy. Short tests can show a 'saving' that is just noise. Longer profiles repeat and interleave settings so drift cancels out.",
           example="RUN2 used quick and one result landed inside the noise band. Standard is the default now."),
        _o("planner", "Who proposes the change",
           [_c("muse", "Muse Spark (Meta API)"), _c("baseten", "Baseten open-weights model"),
            _c("fallback", "Fixed rules, no model")],
           "fallback", group="basics", applies="live", flag="--planner",
           what="The planner reads the telemetry and suggests what to try next. It never applies anything itself.",
           why="A model planner can reason about trade-offs; fixed rules are predictable and free. Either way every proposal passes the same gates and approval.",
           example="Muse Spark read the sweep and proposed 400 W. Its prompt and reply are shown in the run."),
        _o("levers", "What to try",
           [_c("auto", "Everything this model supports"), _c("cap", "Power cap only"),
            _c("topk", "Adaptive top-k only"), _c("effort", "Effort routing only (hosted)"),
            _c("cache", "Cache-friendly order only (hosted)")],
           "auto", group="basics", applies="replay", flag="levers",
           what="The kinds of change WattNot may test.",
           why="Each lever saves energy a different way. Picking one keeps the run short and the result easy to read.",
           example="Power cap lowers the card's watt limit in 50 W steps."),
        _o("cap_range", "Power cap range",
           [_c("700-400", "700 W down to 400 W"), _c("700-500", "700 W down to 500 W (gentle)"),
            _c("700-300", "700 W down to 300 W (aggressive)")],
           "700-400", group="levers", applies="live", flag="--grid",
           what="The highest and lowest power limit to try on the H100. 700 W is stock.",
           why="Below some point the card slows down too much. A range keeps the search inside safe values.",
           example="700 to 400 W in 50 W steps is 7 settings."),
        _o("cap_step", "Cap step size", [_c(25, "25 W"), _c(50, "50 W"), _c(100, "100 W")], 50,
           group="levers", applies="live", flag="--steps",
           what="How far apart the tried caps are.",
           why="Smaller steps find a better cap but take longer to measure.",
           example="50 W steps: 700, 650, 600 ... 400."),
        _o("topk_mass", "Top-k mass", [_c(0.9, "0.90 (saves more)"), _c(0.95, "0.95 (default)"), _c(0.98, "0.98 (safer)")],
           0.95, group="levers", applies="live", flag="--adaptive-mass",
           what="In a Mixture-of-Experts model each token is sent to a few 'experts'. Top-k mass keeps the fewest experts whose router weights add up to this share, and drops the rest.",
           why="Lower mass skips more experts and saves more energy, but answers can drift. The quality gate decides.",
           example="At 0.95, a token whose top 2 experts already carry 95% of the weight skips the other 6."),
        _o("p99_slack_pct", "Slowest 1% may get slower by", [_c(v, f"{v}%") for v in (0, 5, 10, 15, 25)], p99,
           group="gates", applies="replay", flag="--p99-budget-s",
           what="p99 is the response time that 99 of 100 requests beat. This is how much worse it may get.",
           why="Users feel the slow requests, not the average. A change that saves power but makes the worst requests crawl is rejected.",
           example="Stock p99 3.66 s with 10% slack: anything over 4.02 s fails."),
        _o("min_quality_pct", "Answers must agree at least",
           [_c(v, f"{v}%") for v in (90, 94, 95, 97, 99)], q, group="gates", applies="replay", flag="min_quality_pct",
           what="The share of answers that must match the stock model's answers on a fixed test set.",
           why="Saving energy is worthless if the model gets worse. This is the quality gate.",
           example="Granite scored 94.5% at one setting, under a 95% bar, so that setting was not kept."),
        _o("strict_quality", "Keep only quality-checked changes",
           [_c(False, "No: show pending checks too"), _c(True, "Yes: strict")], bool(lim.get("strict_quality", {}).get("default", False)),
           group="gates", applies="replay", flag="strict_quality",
           what="Whether a change can be kept while its quality check is still pending.",
           why="Strict mode is safer for production. Non-strict shows what the energy result would be.",
           example="The Glimmer quality check is pending, so strict mode keeps nothing."),
        _o("approval", "Approval",
           [_c("human", "A person approves high-risk changes"), _c("timeout_reject", "Reject if no answer in 10 min"),
            _c("auto_low", "Auto-approve low risk only")],
           "human", group="gates", applies="live", flag="--auto-approve / --approval-timeout-s",
           what="Who says yes before a change touches the GPU.",
           why="WattNot is advisory by default. A person stays in charge of risky changes, and every decision is logged with who and when.",
           example="A power cap below 450 W is high risk, so it waits for a click. No answer in 10 minutes counts as reject."),
        _o("room", "Room scan", rooms or [_c("example", "Example data center")], "example", group="room", applies="replay",
           flag="room",
           what="A picture of the server room. SAM 3.1 (Meta) outlines each rack so WattNot can show where the heat is.",
           why="It turns a GPU number into a place: which rack gets cooler. The heat drawn is SIMULATED; GPU power is MEASURED.",
           example="12-rack example room. Rack R-07 holds the H100."),
        _o("room_size", "Room size", [_c("s", "Small: 6 racks"), _c("m", "Medium: 12 racks"), _c("l", "Large: 24 racks")], "m",
           group="room", applies="live", flag="--room",
           what="How many racks the example room has.",
           why="Only affects the picture and the room-level estimate, never the measured GPU numbers.",
           example="Medium matches the bundled 12-rack scan."),
        _o("room_layout", "Rack layout", [_c("rows", "Hot/cold aisle rows"), _c("pods", "Pods"), _c("single", "Single row")], "rows",
           group="room", applies="live", flag="--room",
           what="How the racks are arranged.",
           why="Aisle layout changes where heat collects. It is a drawing choice for the example room.",
           example="Hot/cold aisle rows: racks face each other in pairs."),
        _o("gpu", "GPU", [_c("h100", "NVIDIA H100 80GB (E2E Networks)"), _c("none", "No GPU: hosted on Baseten")], "h100",
           group="hardware", applies="live", flag="--gpus / --rig",
           what="The card the test runs on.",
           why="Power can only be measured and capped on a card we control. Hosted APIs give a bill, not watts, so their energy is an ESTIMATE.",
           example="One H100 80GB rented on E2E at about Rs 379 an hour."),
        _o("mode", "Run mode",
           [_c("replay", "Replay a recorded run (free)"), _c("mock", "Mock appliers (free)"),
            _c("live", "Live on a GPU (locked here)")],
           "replay", group="hardware", applies="replay", flag="--replay-only / --live-apply",
           what="Replay re-plays a run we already recorded. Live would rent a GPU and change real settings.",
           why="Live costs money and changes hardware, so this screen never starts it. It needs the owner's okay and the command line.",
           example="Replay uses the H100 data from RUN1 and RUN2."),
    ]
    return {"schema": "wattnot.dash.options/1", "options": o,
            "groups": [{"id": "basics", "label": "Basics"}, {"id": "levers", "label": "Levers"},
                       {"id": "gates", "label": "Safety gates"}, {"id": "room", "label": "Room"},
                       {"id": "hardware", "label": "Hardware and mode"}],
            "live_locked": True}


GOAL_PRESETS = {"save": {"p99_slack_pct": 15, "min_quality_pct": 94},
                "balanced": {"p99_slack_pct": 10, "min_quality_pct": 95},
                "fast": {"p99_slack_pct": 5, "min_quality_pct": 97}}


# Guided stepper (the v2 Configure screen asks one question per screen).
# Every answer maps onto an EXISTING choice key above, so the replay path
# (to_plan_body) needs no change. The stepper never invents a new knob.
STEPPER_SAVE = {
    # save target -> goal preset. Power, cost and water all want the
    # max-saving preset; the replay measures energy and derives cost and
    # water as estimates. Balanced keeps the default limits.
    "power": "save",
    "cost": "save",
    "water": "save",
    "balanced": "balanced",
}
STEPPER_TRAFFIC = {
    # traffic kind -> existing workload id. The workload is used by a live
    # run; a replay always uses the model's recorded workload.
    "chat": "coding",
    "batch": "moe",
    "agents": "classes",
}
STEPPER_CARE = {
    # carefulness -> the existing gate values (same numbers as GOAL_PRESETS).
    "gentle": {"p99_slack_pct": 5, "min_quality_pct": 97},
    "balanced": {"p99_slack_pct": 10, "min_quality_pct": 95},
    "bold": {"p99_slack_pct": 15, "min_quality_pct": 94},
}
STEPPER_COMPARE = ("with", "without", "both")


def apply_stepper(choice: dict, *, save=None, traffic=None, care=None, compare=None) -> dict:
    """Fold stepper answers into an existing choice dict. Unknown answers raise."""
    if save is not None:
        if save not in STEPPER_SAVE:
            raise ValueError(f"save must be one of {sorted(STEPPER_SAVE)}")
        choice["goal"] = STEPPER_SAVE[save]
        choice["save_target"] = save
    if traffic is not None:
        if traffic not in STEPPER_TRAFFIC:
            raise ValueError(f"traffic must be one of {sorted(STEPPER_TRAFFIC)}")
        choice["workload"] = STEPPER_TRAFFIC[traffic]
        choice["traffic"] = traffic
    if care is not None:
        if care not in STEPPER_CARE:
            raise ValueError(f"care must be one of {sorted(STEPPER_CARE)}")
        choice.update(STEPPER_CARE[care])
        choice["care"] = care
    if compare is not None:
        if compare not in STEPPER_COMPARE:
            raise ValueError(f"compare must be one of {list(STEPPER_COMPARE)}")
        choice["compare"] = compare
    return choice


def to_plan_body(choice: dict) -> dict:
    """Map Configure choices to the replay planner's request body. Live is always refused."""
    if choice.get("mode") == "live":
        raise ValueError("live runs cannot be started from the dashboard")
    body: dict = {"model": choice.get("model")}
    for k, v in GOAL_PRESETS.get(choice.get("goal"), {}).items():
        if choice.get(k) is None:
            body[k] = float(v)
    lv = choice.get("levers")
    if lv and lv != "auto":
        body["levers"] = [lv]
    for k in ("p99_slack_pct", "min_quality_pct"):
        if choice.get(k) is not None:
            body[k] = float(choice[k])
    if choice.get("strict_quality") is not None:
        body["strict_quality"] = choice["strict_quality"] in (True, "true", "True", 1)
    if choice.get("room"):
        body["room"] = choice["room"]
    return body
