"""Muse Spark explains why the gate rejected a setting.

After a sweep, every gate failure goes out in ONE batched, offline call (Meta's guidance is
that Spark is not for real-time loops, so this runs after the run, never inside it). Spark
answers per failure with the prompt class most likely hurt and a one-line reason that quotes
the numbers. Only numbers and class counts go out: no prompts, no answers, no host names.

If the call fails, or a reply is missing or names a class we never sent, a rule-based
explanation is used for that failure and it says ``source: rules``. Spark's text is shown as
ADVICE: it explains a MEASURED result, it is not itself a measurement.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping

from wattnot.planner.spark import MODEL

Transport = Callable[[dict], dict]
MAX_REASON = 240

SYSTEM = (
    "You review GPU power-cap trials that a quality/latency gate rejected. For each failure, "
    "name the prompt class most likely responsible (only from the classes given) and give one "
    "short reason that quotes the numbers. Plain words, no hype. JSON only."
)

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["explanations"],
    "properties": {"explanations": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "prompt_class", "reason"],
        "properties": {"id": {"type": "string"}, "prompt_class": {"type": "string"},
                       "reason": {"type": "string", "maxLength": MAX_REASON}}}}},
}


def failures_from_events(events: Iterable[Mapping], classes: Mapping[str, int]) -> list[dict]:
    """Gate failures from a run's event log, as the small rows Spark sees."""

    out = []
    for e in events:
        if e.get("type") != "gate.result":
            continue
        d = e.get("data") or {}
        if d.get("passed") is not False:
            continue
        det = d.get("detail") or {}
        out.append({"id": f"{d.get('trial_no')}-{d.get('check')}", "setting": f"cap {d.get('trial_no')} W",
                    "check": d.get("check"), "score": det.get("score"),
                    "threshold": det.get("threshold"), "unit": det.get("unit", ""),
                    "classes": dict(classes)})
    return out


def request_body(fails: list[dict], model: str = MODEL) -> dict:
    return {"model": model, "reasoning_effort": "minimal", "max_tokens": 900,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": json.dumps(fails, separators=(",", ":"))}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "Explanations", "schema": SCHEMA, "strict": True}}}


def rule_explain(f: Mapping) -> dict:
    """Deterministic explanation from the numbers alone."""

    classes = f.get("classes") or {}
    check = f.get("check")
    if check == "p99":
        # the tail is set by the longest generations; code answers are the longest in proof-v1
        cls = "code" if "code" in classes else (max(classes, key=classes.get) if classes else "all")
        reason = (f"p99 rose {f.get('score')}% against a {f.get('threshold')}% limit. A lower cap "
                  f"lowers the clock, and the longest answers ({cls}) set the tail, so they slow most.")
    elif check in ("quality", "judge"):
        cls = "math" if "math" in classes else (next(iter(classes), "all"))
        reason = (f"quality {f.get('score')} fell under {f.get('threshold')}. Multi-step answers "
                  f"({cls}) are the first to get cut short or drift.")
    elif check == "energy":
        cls = "all"
        reason = "energy did not drop with confidence: the 95% bound still allows no saving."
    else:
        cls = "all"
        reason = f"{check} failed: {f.get('score')} vs limit {f.get('threshold')}."
    return {"id": f["id"], "setting": f.get("setting"), "check": check, "prompt_class": cls,
            "reason": reason[:MAX_REASON], "source": "rules", "label": "ADVICE"}


def explain(fails: list[dict], *, transport: Transport | None = None,
            model: str = MODEL) -> dict:
    """One call for all failures. Returns the ``gate_explanations`` receipt block."""

    if not fails:
        return {"model": None, "calls": 0, "items": [], "note": "no gate failures"}
    rows, meta = {}, {"model": model, "calls": 0, "fallback": None}
    if transport is not None:
        try:
            meta["calls"] = 1
            raw = transport(request_body(fails, model))
            data = json.loads(raw["choices"][0]["message"]["content"])
            known = {f["id"]: f for f in fails}
            for x in data.get("explanations", []):
                f = known.get(x.get("id"))
                if not f or x.get("prompt_class") not in (*(f.get("classes") or {}), "all"):
                    continue
                rows[f["id"]] = {"id": f["id"], "setting": f.get("setting"), "check": f.get("check"),
                                 "prompt_class": x["prompt_class"],
                                 "reason": str(x.get("reason", ""))[:MAX_REASON],
                                 "source": "muse-spark", "label": "ADVICE"}
            u = raw.get("usage") or {}
            meta["tokens"] = {"input": int(u.get("prompt_tokens", 0)),
                              "output": int(u.get("completion_tokens", 0))}
        except Exception as e:  # network, budget, schema
            meta["fallback"] = f"FALLBACK: Spark call failed ({type(e).__name__}), rules used"
    items = [rows.get(f["id"]) or rule_explain(f) for f in fails]
    if transport is None:
        meta["fallback"] = "FALLBACK: no Spark transport, rules used"
    elif not meta["fallback"] and any(i["source"] == "rules" for i in items):
        meta["fallback"] = "FALLBACK: some replies unusable, rules used for those"
    return {**meta, "items": items}


def spark_transport() -> Transport:
    from wattnot.planner.spark import SparkClient

    return SparkClient(model=MODEL, purpose="gate-explain")
