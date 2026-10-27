"""Cache knobs, measured: prompt order and session affinity, plus a half-life probe.

`cache_ab` sends the same small question set three ways over one long shared
document and reads `cached_tokens` back each time:

* volatile-first: a changing timestamp sits at the top, so the prefix never matches,
* stable-first: the document comes first and nothing in it changes,
* stable-first plus an `x-session-affinity` id.

Each arm gets its own copy of the document (a different edition line at the very
top), so one arm cannot warm the cache for another.

`probe_half_life` warms several copies of a prefix, then reads each one back after
a different wait (10 s up to 10 min by default). Each copy is read once, so a
read cannot refresh the entry for the next. The result is the cached share against
wait time: how long a prefix stays warm on the shared pool. It is a measurement of
a shared service and will move with load, so the receipt stores the time it ran.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

from wattnot.cloud import MODEL
from wattnot.cloud.pricing import Rates, cost_usd
from wattnot.cloud.prompts import affinity_headers, build_messages, cached_share, new_session_id
from wattnot.cloud.transport import Request, Transport
from wattnot.cloud.tune import parse_reply

DOC = Path(__file__).resolve().parents[3] / "data" / "cloud" / "shared-prefix.txt"
QUESTIONS = ("Which section covers backups?", "Who is named as owner for each action?",
             "What happens when two readings disagree?", "Which section covers vendors?",
             "What must be attached to the ticket?", "Which section covers access reviews?",
             "What is logged before the work?", "Which section covers capacity planning?")
ARMS = ("volatile-first", "stable-first", "stable-first+affinity")


def load_doc(path: str | Path = DOC) -> str:
    return Path(path).read_text(encoding="utf-8")


def _body(messages: list[dict]) -> dict:
    return {"model": MODEL, "messages": messages, "reasoning_effort": "low", "max_tokens": 400,
            "temperature": 0}


def _messages(arm: str, doc: str, nonce: str, question: str, i: int) -> list[dict]:
    if arm == "volatile-first":
        # a per-call value ahead of the document: the prefix differs on every request
        return build_messages(system=f"Request time: {time.time():.6f} call {i}",
                              shared=[f"Edition {arm}-{nonce}\n{doc}"], task=question)
    return build_messages(shared=[f"Edition {arm}-{nonce}\n{doc}"], task=question)


def cache_ab(transport: Transport, rates: Rates, *, doc: str | None = None, n: int = 8,
             nonce: str | None = None) -> dict:
    doc = doc if doc is not None else load_doc()
    nonce = nonce or uuid.uuid4().hex[:8]
    arms = []
    for arm in ARMS:
        session = new_session_id() if arm.endswith("affinity") else None
        calls = []
        for i in range(n):
            msgs = _messages(arm, doc, nonce, QUESTIONS[i % len(QUESTIONS)], i)
            req = Request(f"cache|{arm}|{i}", _body(msgs), affinity_headers(session))
            calls.append(parse_reply(transport(req), req=req, task_id=f"cache-{i}", cls=arm,
                                     effort="low", repeat=0, rates=rates, session=session))
        prompt = sum(c.prompt for c in calls)
        cached = sum(c.cached for c in calls)
        after_first = calls[1:]
        arms.append({"arm": arm, "calls": n, "prompt_tokens": prompt, "cached_tokens": cached,
                     "cached_share": cached_share(prompt, cached),
                     "cached_share_after_first": cached_share(
                         sum(c.prompt for c in after_first), sum(c.cached for c in after_first)),
                     "usd": round(sum(c.usd for c in calls), 8),
                     # input side only: output length varies call to call and is not a cache effect
                     "input_usd": round(sum(cost_usd(rates, c.prompt, c.cached, 0)
                                            for c in calls), 8),
                     "latency_p50_s": sorted(c.latency_s for c in calls)[n // 2]})
    base = arms[0]["usd"]
    base_in = arms[0]["input_usd"]
    for a in arms:
        a["input_saving_vs_volatile_pct"] = (round(100 * (base_in - a["input_usd"]) / base_in, 2)
                                             if base_in else 0.0)
        a["saving_vs_volatile_pct"] = round(100 * (base - a["usd"]) / base, 2) if base else 0.0
    return {"label": "MEASURED", "arms": arms, "n_per_arm": n, "nonce": nonce}


def probe_half_life(transport: Transport, rates: Rates, *, doc: str | None = None,
                    waits: Sequence[float] = (10, 30, 60, 180, 600),
                    sleep: Callable[[float], None] = time.sleep,
                    clock: Callable[[], float] = time.monotonic) -> dict:
    doc = doc if doc is not None else load_doc()
    nonce = uuid.uuid4().hex[:8]
    q = QUESTIONS[0]

    def send(tag: str, variant: str) -> object:
        msgs = build_messages(shared=[f"Edition probe-{variant}-{nonce}\n{doc}"], task=q)
        req = Request(tag, _body(msgs))
        return parse_reply(transport(req), req=req, task_id=tag, cls="probe", effort="low",
                           repeat=0, rates=rates)

    warm = {w: send(f"probe|warm|{w:g}", f"{w:g}") for w in waits}  # cold writes
    t0 = clock()
    curve = []
    for w in sorted(waits):
        remaining = w - (clock() - t0)
        if remaining > 0:
            sleep(remaining)
        c = send(f"probe|read|{w:g}", f"{w:g}")
        curve.append({"wait_s": w, "prompt_tokens": c.prompt, "cached_tokens": c.cached,
                      "cached_share": cached_share(c.prompt, c.cached), "usd": round(c.usd, 8)})
    top = max((p["cached_share"] for p in curve), default=0.0)
    half = None
    if top > 0:
        for p in curve:
            if p["cached_share"] < top / 2:
                half = p["wait_s"]
                break
    return {"label": "MEASURED", "curve": curve, "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                         time.gmtime()),
            "warm_cached_share": max((cached_share(c.prompt, c.cached) for c in warm.values()),
                                     default=0.0),
            "half_life_upper_bound_s": half,
            "reading": ("no read-back was cached at any wait" if top == 0 else
                        f"cached share fell below half of its best value by {half:g} s" if half
                        else "the prefix was still warm at the longest wait tested")}
