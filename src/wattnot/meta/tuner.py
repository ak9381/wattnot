"""Talk to the tuner: a spoken command ("try 350W with FP8") becomes a Spark plan
that is run through the normal guarded loop in dry-run mode.

The voice guide stays read-only. This module does not touch hardware and does not
apply anything: it

  1. parses the spoken command into a power cap and/or vLLM knobs (Spark's fixed
     allow-list of knobs, so an unknown knob is reported, never guessed),
  2. turns that into one Spark `Decision` per change (the same `Choice`/`Decision`
     the planner emits), clamped to the driver's power range,
  3. files each change as a proposal through the guardrail (`Guard.submit`) in a
     throwaway scratch store, so the keep/reject routing (auto-low-risk vs a person)
     is the real one, and nothing in the repo is changed,
  4. returns a receipt summary plus a short line to be read aloud.

Dry-run is the default and the only path the voice endpoint uses. `live=True` files
the proposals into a real approvals root instead of a scratch one; even then nothing
is auto-applied — a person still approves and applies separately. The spoken summary
is read by the browser's own speech synthesis (labelled "browser voice"), never a
Meta model.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

# H100 SXM defaults (the recorded run's baseline/stock cap is 700 W = card TDP).
H100_STOCK_W = 700
H100_TDP_W = 700
DRIVER_MIN_W = 200
DRIVER_MAX_W = 700
STEP_W = 25

# Spark's planner id and its autonomy tier name (see approvals.risk.DEFAULT_TIERS).
SPARK_PLANNER = {"name": "muse-spark", "model": "muse-spark-1.3", "partner": "meta"}


def _knobs() -> dict[str, tuple]:
    from wattnot.planner.spark import KNOBS

    return KNOBS


def _norm(text: str) -> str:
    """Lowercase, drop hyphens, and fold spoken 'fp 8' / 'f p 8' into 'fp8'."""
    t = (text or "").lower().replace("-", " ")
    t = re.sub(r"\bf\s*p\s*8\b", "fp8", t)
    t = re.sub(r"\bfp\s+8\b", "fp8", t)
    t = re.sub(r"\bfloat\s*8\b", "fp8", t)
    t = re.sub(r"\beight\s*bit\b", "8 bit", t)
    return re.sub(r"\s+", " ", t).strip()


_QUANT_OFF = ("no quantization", "without quantization", "disable quantization",
              "quantization off", "turn off quantization", "full precision",
              "no quant", "bf16", "fp16")
_QUANT_ON = ("fp8", "8 bit", "quantize", "quantization on", "enable quantization",
             "with quantization")


def parse_tune_knobs(text: str) -> tuple[list[tuple[str, Any]], list[str]]:
    """Map spoken knob mentions to (knob, value) pairs from Spark's allow-list.

    Returns (knobs, unsupported) where `unsupported` names things that were heard
    but are not a value Spark allows (so they are reported, never silently applied).
    """
    t = _norm(text)
    knobs = _knobs()
    out: list[tuple[str, Any]] = []
    unsupported: list[str] = []

    # quantization (off wins over on; a bare "quantization" with no value is ambiguous).
    if any(k in t for k in _QUANT_OFF):
        out.append(("quantization", "none"))
    elif any(k in t for k in _QUANT_ON):
        out.append(("quantization", "fp8"))

    if "prefix cach" in t:
        off = any(k in t for k in ("no prefix cach", "disable prefix cach",
                                   "without prefix cach", "prefix caching off",
                                   "turn off prefix cach"))
        out.append(("enable_prefix_caching", not off))

    if "chunked prefill" in t or "chunk prefill" in t:
        off = any(k in t for k in ("no chunked prefill", "disable chunked prefill",
                                   "without chunked prefill", "chunked prefill off",
                                   "turn off chunked prefill"))
        out.append(("enable_chunked_prefill", not off))

    _seq = r"max num seqs|max number of sequences|max sequences|num seqs|batch size|max batch"
    m = re.search(rf"(?:{_seq})\D{{0,8}}(\d{{2,4}})", t) or \
        re.search(rf"(\d{{2,4}})\s*(?:{_seq})", t)
    if m:
        val = int(m.group(1))
        if val in knobs["max_num_seqs"]:
            out.append(("max_num_seqs", val))
        else:
            unsupported.append(f"max_num_seqs={val} (allowed: "
                               f"{', '.join(str(v) for v in knobs['max_num_seqs'])})")

    m = re.search(r"(?:gpu mem(?:ory)? util(?:ization)?|memory utilization)\D{0,8}"
                  r"(\d{1,3}(?:\.\d+)?)\s*(%?)", t)
    if m:
        raw = float(m.group(1))
        val = round(raw / 100, 2) if (m.group(2) == "%" or raw > 1) else round(raw, 2)
        if val in knobs["gpu_mem_util"]:
            out.append(("gpu_mem_util", val))
        else:
            unsupported.append(f"gpu_mem_util={val} (allowed: "
                               f"{', '.join(str(v) for v in knobs['gpu_mem_util'])})")
    return out, unsupported


def parse_tune_command(text: str) -> dict[str, Any]:
    """Parse a spoken tuning command into {cap_w, knobs, unsupported}."""
    from wattnot.meta.voiceguide import _parse_cap_w

    knobs, unsupported = parse_tune_knobs(text)
    return {"cap_w": _parse_cap_w(text), "knobs": knobs, "unsupported": unsupported}


_QUESTION_STARTS = ("what", "why", "how", "is ", "are ", "which", "describe",
                    "explain", "tell me", "does ", "do ", "can ")
_ACTION_VERBS = ("try", "set", "use", "switch", "change", "apply", "enable", "disable",
                 "turn on", "turn off", "turn ", "run", "cap", "lower", "raise", "drop",
                 "bump", "tune")
_TUNER_VERBS = ("talk to the tuner", "the tuner", "retune", "re tune", "tune the",
                "run the tuner", "ask spark", "ask the tuner", "tuner")


def is_tune_command(text: str) -> bool:
    """True when the text is an imperative tuning command (not a question).

    A command either names the tuner directly, or names a Spark knob together with
    an action verb or a power cap. Questions and "what does X mean" are never
    commands (they stay answer/explain).
    """
    t = _norm(text)
    if not t:
        return False
    if any(t.startswith(q) for q in _QUESTION_STARTS):
        return False
    if "mean" in t:
        return False
    if any(v in t for v in _TUNER_VERBS):
        return True
    knobs, _ = parse_tune_knobs(text)
    if not knobs:
        return False
    from wattnot.meta.voiceguide import _parse_cap_w

    has_cap = _parse_cap_w(text) is not None
    has_action = any(v in t for v in _ACTION_VERBS)
    return has_cap or has_action


def _clamp_cap(want: int, *, min_w: int, max_w: int) -> tuple[int, bool]:
    """Clamp a requested cap to the driver's power range (hardware safety only).

    Unlike the sweep loop's one-step clamp, a direct command is clamped only to the
    driver min/max; how big a drop needs a person is left to the guardrail risk.
    """
    safe = max(min_w, min(max_w, int(want)))
    return safe, safe != int(want)


def plan_with_spark(parsed: Mapping, *, current_cap_w: int = H100_STOCK_W,
                    min_w: int = DRIVER_MIN_W, max_w: int = DRIVER_MAX_W) -> dict[str, Any]:
    """Turn the parsed command into Spark `Decision`s (one per change), clamped safely."""
    from wattnot.planner.spark import MODEL, Choice, Decision

    decisions: list[Decision] = []
    cap_w = parsed.get("cap_w")
    if isinstance(cap_w, int):
        safe, clamped = _clamp_cap(cap_w, min_w=min_w, max_w=max_w)
        reason = (f"voice command requested {cap_w} W" if not clamped
                  else f"voice command requested {cap_w} W; clamped to the "
                       f"{min_w}-{max_w} W driver range")
        decisions.append(Decision(Choice("cap", power_cap_w=safe), reason, MODEL,
                                  clamped=clamped))
    for knob, value in parsed.get("knobs", []):
        decisions.append(Decision(Choice("knob", knob=knob, value=value),
                                  f"voice command requested {knob}={value}", MODEL))
    return {"decisions": decisions, "unsupported": list(parsed.get("unsupported", []))}


def _cap_spec(cap_w: int, *, stock_w: int, gpu: int = 0) -> dict:
    drop = None if not stock_w else round((stock_w - cap_w) / stock_w * 100, 1)
    return {
        "kind": "power_cap",
        "title": f"Set GPU {gpu} power cap to {cap_w} W (from {stock_w} W)",
        "summary": (f"Dry-run plan from a voice command: lower GPU {gpu} power limit "
                    f"from {stock_w} W to {cap_w} W. Nothing is applied; the guardrail "
                    "only classifies and routes this change."),
        "change": {"target": f"gpu:{gpu}", "gpu": gpu,
                   "before": {"power_cap_w": stock_w}, "after": {"power_cap_w": cap_w},
                   "drop_pct": drop},
    }


_KNOB_BEFORE = {"quantization": "none", "enable_prefix_caching": False,
                "enable_chunked_prefill": False}


def _knob_spec(knob: str, value: Any) -> dict:
    before = {knob: _KNOB_BEFORE.get(knob)} if knob in _KNOB_BEFORE else {}
    return {
        "kind": "vllm_config",
        "title": f"Change vLLM setting: {knob} -> {value}",
        "summary": (f"Dry-run plan from a voice command: restart vLLM with {knob}={value}. "
                    "Nothing is applied; the guardrail only classifies and routes it."),
        "change": {"target": "vllm", "before": before, "after": {knob: value},
                   "changed": [knob]},
    }


def _change_line(spec: Mapping) -> str:
    ch = spec["change"]
    if spec["kind"] == "power_cap":
        return f"GPU power cap {ch['before']['power_cap_w']} W -> {ch['after']['power_cap_w']} W"
    knob = ch["changed"][0]
    return f"{knob} {ch['before'].get(knob)} -> {ch['after'][knob]}"


# Speech-friendly spellings so the browser voice reads them clearly.
_SPEAK_VALUE = {"fp8": "F P 8", "none": "none", True: "on", False: "off"}


def _speak_change(spec: Mapping) -> str:
    ch = spec["change"]
    if spec["kind"] == "power_cap":
        return f"set the GPU power cap to {ch['after']['power_cap_w']} watts"
    knob = ch["changed"][0]
    val = ch["after"][knob]
    spoken_val = _SPEAK_VALUE.get(val, str(val))
    knob_words = knob.replace("enable_", "").replace("_", " ")
    if knob == "quantization":
        return f"switch quantization to {spoken_val}"
    if knob in ("enable_prefix_caching", "enable_chunked_prefill"):
        return f"turn {spoken_val} {knob_words}"
    return f"set {knob_words} to {spoken_val}"


def run_tune(text: str, *, root_dir: str | Path | None = None, live: bool = False,
             stock_cap_w: int = H100_STOCK_W, tdp_w: int = H100_TDP_W,
             min_w: int = DRIVER_MIN_W, max_w: int = DRIVER_MAX_W) -> dict[str, Any]:
    """Plan a spoken tuning command with Spark and run it through the guarded loop.

    Dry-run (default): a throwaway scratch approvals store, so the real keep/reject
    routing runs but nothing in the repo changes and no hardware is touched. The
    result carries a `speech` line for the browser voice and a `guard` receipt summary.
    """
    from wattnot.approvals.guard import Guard
    from wattnot.approvals.service import Approvals

    parsed = parse_tune_command(text)
    plan = plan_with_spark(parsed, current_cap_w=stock_cap_w, min_w=min_w, max_w=max_w)
    decisions = plan["decisions"]
    mode = "live" if live else "dry-run"
    label = "ESTIMATE" if live else "MOCK"

    planner_view = {"name": "Spark", "model": SPARK_PLANNER["model"],
                    "note": ("planned offline for a dry run; no paid Spark call"
                             if not live else "planned for review; no paid Spark call")}

    if not decisions:
        hint = ("I heard a tuning command but could not map it to a power cap or a "
                "known knob. Try, for example, 'try 350 watts with FP8'.")
        return {
            "intent": "command", "action": "tune", "mode": mode, "label": label,
            "text": hint,
            "speech": "I could not map that to a cap or a known knob. "
                      "Try, for example, try 350 watts with F P 8.",
            "voice": _voice_meta(),
            "plan": {"command": text, "planner": planner_view,
                     "decisions": [], "unsupported": plan["unsupported"]},
            "guard": {"changes": [], "receipt": None},
        }

    # Build one proposal spec per decision.
    specs: list[dict] = []
    for d in decisions:
        c = d.choice
        if c.kind == "cap":
            specs.append(_cap_spec(c.power_cap_w, stock_w=stock_cap_w))
        elif c.kind == "knob":
            specs.append(_knob_spec(c.knob, c.value))

    scratch = None
    try:
        if live:
            if root_dir is None:
                raise ValueError("live tuning needs a root_dir for the approvals store")
            approvals = Approvals(Path(root_dir))
        else:
            scratch = tempfile.mkdtemp(prefix="wattnot-tuner-dryrun-")
            approvals = Approvals(scratch)
        guard = Guard(approvals)
        changes: list[dict] = []
        pids: list[str] = []
        for spec in specs:
            res = guard.submit(spec, gates_passed=True, stock_cap_w=stock_cap_w,
                               tdp_w=tdp_w, planner=dict(SPARK_PLANNER))
            prop, risk, route = res["proposal"], res["risk"], res["route"]
            pids.append(prop["id"])
            changes.append({
                "kind": spec["kind"],
                "title": spec["title"],
                "change": _change_line(spec),
                "speak": _speak_change(spec),
                "risk": risk.level,
                "reasons": list(risk.reasons),
                "route": route,
                "needs_person": route in ("pending", "pending-strict"),
                "proposal_id": prop["id"],
                "proposal_sha256": prop["content_sha256"],
                "label": prop["label"],
            })
        receipt = guard.receipt_block(pids)
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)

    text_summary, speech = _summaries(text, changes, plan["unsupported"], mode)
    return {
        "intent": "command", "action": "tune", "mode": mode, "label": label,
        "text": text_summary,
        "speech": speech,
        "voice": _voice_meta(),
        "plan": {
            "command": text, "planner": planner_view,
            "decisions": [{"kind": d.choice.kind, "power_cap_w": d.choice.power_cap_w,
                           "knob": d.choice.knob, "value": d.choice.value,
                           "reason": d.reason, "clamped": d.clamped}
                          for d in decisions],
            "unsupported": plan["unsupported"],
        },
        "guard": {"changes": changes, "receipt": receipt},
    }


def _voice_meta() -> dict:
    return {"engine": "browser speechSynthesis", "label": "browser voice",
            "note": "read aloud by your browser's built-in voice, not a Meta model"}


def _summaries(command: str, changes: list[dict], unsupported: list[str],
               mode: str) -> tuple[str, str]:
    """Display text and a speech-friendly line summarising the guarded plan."""
    n = len(changes)
    prefix = "Dry run — nothing was changed." if mode == "dry-run" else \
             "Live plan — filed for a person to approve; nothing was changed yet."

    display = [f"{prefix} Spark planned {n} change{'s' if n != 1 else ''} from your command:"]
    for i, c in enumerate(changes, 1):
        verdict = ("a person must approve it" if c["needs_person"]
                   else "the guardrail auto-approves it (low risk)")
        why = f" ({c['reasons'][0]})" if c["reasons"] else ""
        display.append(f"{i}. {c['change']} — {verdict}{why}.")
    if unsupported:
        display.append("Not planned (unknown to Spark): " + "; ".join(unsupported) + ".")

    speak_parts = [f"{'Dry run' if mode == 'dry-run' else 'Live plan'}. "
                   f"Spark planned {n} change{'s' if n != 1 else ''}."]
    for c in changes:
        verdict = "a person must approve this" if c["needs_person"] else \
                  "the guardrail auto-approves this"
        speak_parts.append(f"{c['speak'].capitalize()}: {verdict}.")
    speak_parts.append("Nothing was changed." if mode == "dry-run"
                       else "Filed for approval; nothing was changed yet.")
    return " ".join(display), " ".join(speak_parts)
