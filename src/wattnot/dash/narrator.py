"""The Muse Spark narration worker for the dashboard replay.

A run's event stream (the ``plan()`` frames) carries one ``step.started`` per step with
``data.narration = {"text": ..., "source": "template"}`` -- a rule-based line written offline.
Two of those steps are rule-based placeholders meant to be upgraded by a live model: the room
"pick" step and the sweep "pick" step. This module reads the facts out of their events, asks a
GUARDED Muse Spark client for a one- or two-sentence line per step, and records the replies to a
cache file. The dashboard reads the RECORDED live text from that cache and labels it LIVE with the
model id and the reply's trace id. When there is no cache (offline), the rule-based "template" line
stays, clearly labelled.

Nothing here talks to a network at import time. The client is injected; the default client is built
lazily (and only when actually generating). Every value this module produces is honest: a line it
cannot get from the live model is skipped, never faked, and the recorded lines are labelled
``source="muse-spark"`` / ``label="LIVE"`` with the real model id and trace id.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

#: The step kinds (node.sub) that get a live Muse Spark line. These are the two rule-based
#: placeholders in runplan.py (room "pick" and sweep "pick"). Identified by kind, not by the
#: ``#k`` suffix, so the first of each in a run is narrated.
NARRATED_KINDS = ("room.pick", "sweep.pick")

SOURCE = "muse-spark"
LABEL = "LIVE"
DEFAULT_MODEL = "muse-spark-1.3"

#: Where a run's narration cache lives. Parameterizable everywhere; this is only the default root.
CACHE_ROOT = Path("data") / "narration-1031"

_SYSTEM = (
    "You narrate one step of a GPU energy-tuning run for a general audience. "
    "Write one or two short, plain sentences describing what this step decided and why, "
    "using only the facts given. Do not invent numbers. Do not use the words 'I' or 'as an AI'. "
    "No preamble, no markdown, just the sentence(s)."
)


# ---------------------------------------------------------------- reading the facts

def _data(frame: dict) -> dict:
    return ((frame.get("event") or {}).get("data")) or {}


def _kind(frame: dict) -> str | None:
    ev = frame.get("event") or {}
    return _data(frame).get("kind") if ev.get("type") == "step.started" else None


def _facts_for(frames: list[dict], step_id: str) -> dict:
    """Collect the honest facts a step's narration may use: the started label/inputs, the decision
    extra (choice/reason/model), any numbers on the finished event. All read from the stream."""
    facts: dict = {"numbers": []}
    for f in frames:
        ev = f.get("event") or {}
        if ev.get("step_id") != step_id:
            continue
        d = _data(f)
        typ = ev.get("type")
        if typ == "step.started":
            facts["label"] = d.get("label")
            facts["inputs"] = d.get("inputs")
            facts["template"] = (d.get("narration") or {}).get("text")
        elif typ == "decision":
            facts["decision"] = {k: d.get(k) for k in ("choice", "reason", "model") if k in d}
        elif typ == "step.finished":
            facts["summary"] = d.get("summary")
            facts["outputs"] = d.get("outputs")
            for n in d.get("numbers") or []:
                if n.get("v") is not None:
                    facts["numbers"].append({"label": n.get("label"), "v": n.get("v"), "unit": n.get("unit")})
    return facts


def narrated_step_ids(frames: list[dict]) -> list[str]:
    """The step_ids in this stream that should get a live line (room pick, sweep pick), in order."""
    out = []
    for f in frames:
        if _kind(f) in NARRATED_KINDS:
            sid = (f.get("event") or {}).get("step_id")
            if sid and sid not in out:
                out.append(sid)
    return out


# ---------------------------------------------------------------- the prompt and the live call

def _prompt(facts: dict) -> str:
    lines = []
    if facts.get("label"):
        lines.append(f"Step: {facts['label']}")
    dec = facts.get("decision") or {}
    if dec.get("choice"):
        lines.append(f"Choice: {json.dumps(dec['choice'], sort_keys=True)}")
    if dec.get("reason"):
        lines.append(f"Reason given by the rule: {dec['reason']}")
    if facts.get("inputs"):
        lines.append(f"Inputs: {facts['inputs']}")
    if facts.get("outputs"):
        lines.append(f"Outputs: {facts['outputs']}")
    for n in facts.get("numbers", []):
        unit = f" {n['unit']}" if n.get("unit") else ""
        lines.append(f"- {n['label']}: {n['v']}{unit}")
    return "The facts for this step:\n" + "\n".join(lines)


def _trace_id(reply: dict, step_id: str, text: str) -> str:
    """The reply's own id if the API gave one, otherwise a stable synthetic id (hash of the
    step and the text) labelled as synthesised so no fake trace id is passed off as real."""
    for k in ("id", "response_id", "trace_id"):
        v = reply.get(k)
        if isinstance(v, str) and v:
            return v
    h = hashlib.sha256(f"{step_id}\n{text}".encode()).hexdigest()[:16]
    return f"synth-{h}"


def narrate(frames: list[dict], *, client=None, model: str = DEFAULT_MODEL) -> dict:
    """Live-narrate the pick steps in ``frames``.

    For each narrated step, build a compact prompt from that step's facts, call the guarded Muse
    Spark ``client`` and return ``{step_id: {"text", "source", "model", "trace_id", "label"}}``.
    A step whose reply is missing or empty is skipped (never raises). ``client`` is a callable
    ``client(body, purpose=...) -> dict`` (a GuardedClient or a job-capped wrapper); the default
    builds a GuardedClient lazily.
    """
    if client is None:
        client = _default_client()
    out: dict = {}
    for sid in narrated_step_ids(frames):
        facts = _facts_for(frames, sid)
        body = {"model": model, "temperature": 0, "max_tokens": 120, "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _prompt(facts)}]}
        try:
            reply = client(body, purpose="dash-narrate")
            text = ((reply.get("choices") or [{}])[0].get("message") or {}).get("content")
            text = (text or "").strip()
        except Exception:
            continue
        if not text:
            continue
        out[sid] = {"text": text, "source": SOURCE, "model": str(reply.get("model") or model),
                    "trace_id": _trace_id(reply, sid, text), "label": LABEL}
    return out


def _default_client():
    """Build a GuardedClient only when a live call is actually about to happen (no network/import
    at module load). The caller normally injects a client, so this is a last resort."""
    from wattnot.meta.client import GuardedClient
    return GuardedClient()


# ---------------------------------------------------------------- the cache layer

def cache_path_for(run_id: str, root: str | Path | None = None) -> Path:
    """Where a run's narration cache lives: ``<root>/<run_id>.json`` (default root ``data/narration-1031``)."""
    return Path(root or CACHE_ROOT) / f"{run_id}.json"


def generate_and_cache(frames: list[dict], cache_path: str | Path, *, client=None,
                       model: str = DEFAULT_MODEL) -> dict:
    """Narrate the pick steps live and write the result as JSON to ``cache_path``. Returns the dict."""
    narr = narrate(frames, client=client, model=model)
    p = Path(cache_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(narr, indent=1, sort_keys=True) + "\n")
    return narr


def load_cache(cache_path: str | Path) -> dict:
    """Read a narration cache written by ``generate_and_cache``. Missing file -> empty dict.

    Only well-formed muse-spark entries are returned; anything else is dropped so the replay can
    never label a bad or mislabelled line LIVE.
    """
    p = Path(cache_path)
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    out = {}
    for sid, v in doc.items():
        if (isinstance(v, dict) and isinstance(v.get("text"), str) and v["text"].strip()
                and v.get("source") == SOURCE and v.get("model") and v.get("trace_id")):
            out[sid] = {"text": v["text"], "source": SOURCE, "model": str(v["model"]),
                        "trace_id": str(v["trace_id"]), "label": LABEL}
    return out


__all__ = ["CACHE_ROOT", "NARRATED_KINDS", "cache_path_for", "generate_and_cache",
           "load_cache", "narrate", "narrated_step_ids"]
