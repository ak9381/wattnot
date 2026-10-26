"""Smoke test for the Muse Spark planner and judge calls (task T08).

Sends dummy telemetry only. Reads the key from META_API_KEY.
Usage: META_API_KEY=... python scripts/planner_smoke.py [--record PATH]
"""
from __future__ import annotations

import argparse, json, os, sys, time, urllib.request
from datetime import datetime, timezone

from jsonschema import Draft202012Validator

BASE = os.environ.get("META_BASE_URL", "https://api.meta.ai/v1")
MODEL = os.environ.get("WATTNOT_PLANNER_MODEL", "muse-spark-1.3")

TRIAL_PROPOSAL = {
    "type": "object", "additionalProperties": False,
    "required": ["knob", "params", "reason", "expected_wh_delta_pct", "risk"],
    "properties": {
        "knob": {"enum": ["power_cap_w", "batch_size", "kv_cache_dtype", "weights_dtype"]},
        "params": {"type": "object", "additionalProperties": False,
            "properties": {
                "power_cap_w": {"type": ["integer", "null"], "minimum": 200, "maximum": 700},
                "batch_size": {"type": ["integer", "null"], "minimum": 1, "maximum": 256},
                "kv_cache_dtype": {"enum": ["fp16", "fp8", None]},
                "weights_dtype": {"enum": ["bf16", "fp8", None]}},
            "required": ["power_cap_w", "batch_size", "kv_cache_dtype", "weights_dtype"]},
        "reason": {"type": "string", "maxLength": 400},
        "expected_wh_delta_pct": {"type": "number", "minimum": -90, "maximum": 0},
        "risk": {"enum": ["low", "medium", "high"]},
    },
}
JUDGE_VERDICT = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "reason"],
    "properties": {"verdict": {"enum": ["pass", "fail"]},
                   "reason": {"type": "string", "maxLength": 400}},
}

TELEMETRY = {  # fake numbers, not from any real run
    "gpu": "H100-SXM-80GB (dummy)", "power_w": 612, "power_cap_w": 700,
    "sm_util_pct": 71, "mem_used_gb": 58.4, "batch_size": 16,
    "kv_cache_dtype": "fp16", "weights_dtype": "bf16",
    "p99_latency_ms": 840, "slo_p99_ms": 1200, "wh_per_1k_tokens": 0.92,
}
KNOBS = ["power_cap_w (200-700)", "batch_size (1-256)",
         "kv_cache_dtype (fp16|fp8)", "weights_dtype (bf16|fp8)"]
QUALITY = {
    "baseline": {"exact_match": 0.812, "rubric_mean": 4.31, "n": 200},
    "candidate": {"exact_match": 0.806, "rubric_mean": 4.27, "n": 200,
                  "change": "kv_cache_dtype fp16 -> fp8"},
    "rule": "fail if exact_match drops more than 1.0 point or rubric_mean drops more than 0.1",
}


def call(messages, schema, name):
    body = {"model": MODEL, "messages": messages, "reasoning_effort": "low",
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": name, "schema": schema, "strict": True}}}
    req = urllib.request.Request(f"{BASE}/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {os.environ['META_API_KEY']}",
                 "Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            status, raw = r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        status, raw = e.code, json.loads(e.read() or b"{}")
    return status, round((time.monotonic() - t0) * 1000), raw


def check(label, messages, schema, name):
    status, ms, raw = call(messages, schema, name)
    out = {"label": label, "status": status, "latency_ms": ms,
           "model": raw.get("model"), "usage": raw.get("usage"), "valid": False}
    if status == 200:
        text = raw["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(text)
            errs = [e.message for e in Draft202012Validator(schema).iter_errors(parsed)]
            if name == "TrialProposal" and not errs:
                set_ = [k for k, v in parsed["params"].items() if v is not None]
                if set_ != [parsed["knob"]]:
                    errs.append(f"expected only {parsed['knob']} set, got {set_}")
            out.update(output=parsed, valid=not errs, errors=errs)
        except json.JSONDecodeError as e:
            out.update(output=text, errors=[str(e)])
    else:
        out["error"] = raw.get("error")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record")
    a = ap.parse_args()
    if not os.environ.get("META_API_KEY"):
        sys.exit("META_API_KEY is not set")
    plan = check("planner", [
        {"role": "system", "content": "You plan the next energy trial for an inference server. "
         "Change exactly one knob: set its new value in params and leave the others null. "
         "Keep p99 under the SLO. Reply with JSON only."},
        {"role": "user", "content": json.dumps({"telemetry": TELEMETRY, "knobs": KNOBS})}],
        TRIAL_PROPOSAL, "TrialProposal")
    judge = check("judge", [
        {"role": "system", "content": "You judge whether a candidate config keeps quality. "
         "Apply the rule strictly. Reply with JSON only."},
        {"role": "user", "content": json.dumps(QUALITY)}], JUDGE_VERDICT, "JudgeVerdict")
    result = {"recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "calls": [plan, judge]}
    print(json.dumps(result, indent=2))
    if a.record:
        with open(a.record, "w") as f:
            json.dump(result, f, indent=2)
            f.write("\n")
    sys.exit(0 if plan["valid"] and judge["valid"] else 1)


if __name__ == "__main__":
    main()
