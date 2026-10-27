"""The reasoning-effort router.

For each prompt class in the workload:

1. Run the reference (`xhigh`, the API default) on every item. That is what an
   untuned caller pays, and it is the answer key for the gate.
2. Try the cheaper levels in the order the agent proposed. Each level is gated:
   the existing fixed-check quality gate against the reference, then the p99
   gate. A level that passes makes every more expensive level pointless, so
   those are skipped.
3. Keep the lowest level that passes. If none does, the class stays on `xhigh`.

The p99 gate counts the escape hatch. In serving, an answer that fails its cheap
check is retried once at `xhigh`. In the replay that retry is charged to the
candidate (its latency and its cost), so a level that only looks fast because it
fails often is caught.

Nothing here is a guess about quality. If a call returns no answer, or runs out
of tokens while reasoning (`finish_reason: length`), that item counts as a fail.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from wattnot.cloud import EFFORTS, MODEL, REFERENCE
from wattnot.cloud import efforts as ef
from wattnot.cloud.pricing import Rates, cost_usd
from wattnot.cloud.prompts import (
    affinity_headers,
    build_messages,
    cached_share,
    hashed,
    new_session_id,
    prefix_hash,
    volatile_in_prefix,
)
from wattnot.cloud.transport import CloudError, Reply, Request, Transport
from wattnot.gates.fixed import fixed_checks
from wattnot.gates.latency import p99_gate
from wattnot.runner.trial import percentile
from wattnot.runner.workload import WorkloadItem

SYSTEM_TEXT = ("You are a careful assistant. Follow the requested answer format exactly and "
               "put the final answer on its own line.")
RANK = {e: i for i, e in enumerate(EFFORTS)}


class BudgetStop(RuntimeError):
    """Spend reached the stop line (80% of the budget)."""


@dataclass(frozen=True)
class Lat:
    """What `p99_gate` needs from a trial."""

    p99_s: float


@dataclass
class Call:
    task_id: str
    cls: str
    effort: str
    repeat: int
    status: int
    usable: bool  # got a complete, non-empty answer
    finish_reason: str | None
    content: str = field(default="", repr=False)  # in memory only; never written out
    prompt: int = 0
    cached: int = 0
    completion: int = 0
    reasoning: int | None = None
    reasoning_source: str = "absent"  # api | estimated_from_text | absent
    latency_s: float = 0.0
    retries: int = 0
    usd: float = 0.0
    session: str | None = None
    prefix: str | None = None
    fields: dict = field(default_factory=dict)
    rate_headers: dict = field(default_factory=dict)
    answer_sha: str | None = None
    sent_at: float = 0.0
    tag: str = ""

    def row(self) -> dict:
        """The line written to calls.jsonl: numbers and hashes, no text."""
        return {"tag": self.tag or f"{self.task_id}|{self.effort}|{self.repeat}", "class": self.cls,
                "effort": self.effort, "status": self.status, "usable": self.usable,
                "finish_reason": self.finish_reason, "prompt_tokens": self.prompt,
                "cached_tokens": self.cached, "completion_tokens": self.completion,
                "reasoning_tokens": self.reasoning, "reasoning_source": self.reasoning_source,
                "latency_s": round(self.latency_s, 4), "retries": self.retries,
                "usd": round(self.usd, 8), "session_sha": self.session, "prefix_sha": self.prefix,
                "answer_sha256": self.answer_sha, "usage_fields": self.fields,
                "rate_limit": self.rate_headers}


def parse_reply(reply: Reply, *, req: Request, task_id: str, cls: str, effort: str, repeat: int,
                rates: Rates, session: str | None = None, prefix: str | None = None) -> Call:
    body = reply.body if isinstance(reply.body, Mapping) else {}
    usage = body.get("usage") or {}
    choice = (body.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    finish = choice.get("finish_reason")
    ptd = usage.get("prompt_tokens_details") or {}
    ctd = usage.get("completion_tokens_details") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    cached = int(ptd.get("cached_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    # Reasoning tokens: trust the API field if it is filled; otherwise estimate from the text.
    api_r = ctd.get("reasoning_tokens")
    text_chars = msg.get("reasoning_chars")
    text = msg.get("reasoning_content") or msg.get("reasoning")
    if text_chars is None and isinstance(text, str):
        text_chars = len(text)
    if isinstance(api_r, int) and api_r > 0:
        reasoning, source = api_r, "api"
    elif text_chars:
        reasoning, source = max(1, round(text_chars / 4)), "estimated_from_text"
    else:
        reasoning, source = None, "absent"
    fields = {"cached_tokens": "cached_tokens" in ptd, "reasoning_tokens": api_r is not None,
              "reasoning_text": bool(text_chars),
              "accepted_prediction_tokens": "accepted_prediction_tokens" in ctd}
    usable = reply.status == 200 and bool(content.strip()) and finish != "length"
    return Call(task_id, cls, effort, repeat, reply.status, usable, finish, content, prompt,
                cached, completion, reasoning, source, reply.latency_s, reply.retries,
                cost_usd(rates, prompt, cached, completion) if reply.status == 200 else 0.0,
                hashed(session), prefix, fields, reply.headers,
                hashlib.sha256(content.encode()).hexdigest()[:16] if content else None,
                reply.sent_at, req.tag)


MAX_BAD_STREAK = 3


def req_tag(c: Call) -> str:
    return c.tag or f"{c.task_id}|{c.effort}|{c.repeat}"


class Runner:
    """Makes the calls, tracks spend, and refuses to cross the stop line."""

    def __init__(self, transport: Transport, rates: Rates, *,
                 allowance: Mapping[str, int] = ef.DEFAULT_ALLOWANCE,
                 budget_usd: float | None = None, stop_at: float = 0.8,
                 affinity: bool = True) -> None:
        self.transport = transport
        self.rates = rates
        self.allowance = dict(allowance)
        self.budget_usd = budget_usd
        self.stop_at = stop_at
        self.affinity = affinity
        self.spent = 0.0
        self.calls: list[Call] = []
        self.sessions: dict[str, str] = {}
        self.volatile: set[str] = set()
        self.bad_streak = 0

    def session_for(self, cls: str) -> str | None:
        if not self.affinity:
            return None
        return self.sessions.setdefault(cls, new_session_id())

    def body_for(self, item: WorkloadItem, effort: str) -> dict:
        body = {"model": MODEL,
                "messages": build_messages(system=SYSTEM_TEXT, task=item.prompt),
                "reasoning_effort": effort, "temperature": 0,
                "max_tokens": ef.max_tokens_for(effort, item.max_tokens, self.allowance)}
        stop = ef.stop_for(effort, item.meta.get("stop", []))
        if stop:
            body["stop"] = stop
        return body

    def call(self, item: WorkloadItem, effort: str, repeat: int) -> Call:
        if self.budget_usd is not None and self.spent >= self.stop_at * self.budget_usd:
            raise BudgetStop(f"spent ${self.spent:.4f} of ${self.budget_usd:.4f}; "
                             f"stopping at {self.stop_at:.0%}")
        cls = item.meta.get("class", "default")
        body = self.body_for(item, effort)
        session = self.session_for(cls)
        self.volatile.update(volatile_in_prefix(body["messages"]))
        req = Request(f"{item.id}|{effort}|{repeat}", body, affinity_headers(session))
        reply = self.transport(req)
        c = parse_reply(reply, req=req, task_id=item.id, cls=cls, effort=effort, repeat=repeat,
                        rates=self.rates, session=session, prefix=prefix_hash(body["messages"]))
        self.spent += c.usd
        self.calls.append(c)
        self._stop_on_errors(c, reply)
        return c

    def _stop_on_errors(self, c: Call, reply: Reply) -> None:
        """A live run must not burn money on a broken setup: stop on a hard error or a streak."""

        if c.status == 200:
            self.bad_streak = 0
            return
        err = reply.body.get("error") if isinstance(reply.body, Mapping) else None
        msg = (err.get("message") if isinstance(err, Mapping) else str(err or ""))[:200]
        self.bad_streak += 1
        hard = 400 <= c.status < 500 and c.status not in (408, 429)
        if hard or self.bad_streak >= MAX_BAD_STREAK:
            raise CloudError(f"stopping: HTTP {c.status} on {req_tag(c)} "
                             f"({self.bad_streak} in a row): {msg}")


# ---- proposal -------------------------------------------------------------------------------

def propose(classes: Mapping[str, Sequence[WorkloadItem]], transport: Transport | None,
            rates: Rates) -> dict:
    """Ask the agent for an order per class. Any failure falls back to low, medium, high."""

    names = list(classes)
    fallback = {n: ef.default_order() for n in names}
    if transport is None:
        return {"orders": fallback, "proposer": "fallback", "note": "planner not used",
                "tokens": {"input": 0, "output": 0}, "usd": 0.0}
    summary = ef.summarize_classes(classes)
    req = Request("propose", ef.proposal_body(summary))
    call = None
    try:
        reply = transport(req)
        call = parse_reply(reply, req=req, task_id="propose", cls="planner", effort="low",
                           repeat=0, rates=rates)
        orders = ef.parse_orders(call.content, names)
        try:
            note = " ".join(str(json.loads(call.content[call.content.index("{"):
                                                       call.content.rindex("}") + 1])
                                .get("note", "")).split())[:200]
        except ValueError:
            note = ""
        return {"orders": orders, "proposer": "muse-spark", "note": note,
                "tokens": {"input": call.prompt, "output": call.completion}, "usd": call.usd,
                "_call": call}
    except Exception as e:  # network, shape, parse: the fixed order is always safe
        return {"orders": fallback, "proposer": "fallback", "_call": call,
                "note": f"proposal unusable ({type(e).__name__}); used low, medium, high",
                "tokens": {"input": 0, "output": 0}, "usd": call.usd if call else 0.0}


# ---- one class ------------------------------------------------------------------------------

def _expand(items: Sequence[WorkloadItem], repeats: int) -> list[WorkloadItem]:
    return [WorkloadItem(f"{i.id}#{r}", i.prompt, i.max_tokens, i.meta)
            for r in range(repeats) for i in items]


def _outputs(calls: Sequence[Call]) -> dict[str, str]:
    return {f"{c.task_id}#{c.repeat}": c.content for c in calls if c.usable}


def totals(calls: Sequence[Call]) -> dict:
    n = len(calls)
    lat = [c.latency_s for c in calls]
    known = [c.reasoning for c in calls if c.reasoning is not None]
    sources = {c.reasoning_source for c in calls}
    return {
        "requests": n,
        "prompt_tokens": sum(c.prompt for c in calls),
        "cached_tokens": sum(c.cached for c in calls),
        "completion_tokens": sum(c.completion for c in calls),
        "reasoning_tokens": sum(known) if known else None,
        "reasoning_source": ("api" if sources == {"api"} else "absent" if sources == {"absent"}
                             else "mixed"),
        "usd": round(sum(c.usd for c in calls), 8),
        "latency_p50_s": round(percentile(lat, 50), 4) if lat else None,
        "latency_p99_s": round(percentile(lat, 99), 4) if lat else None,
        "truncated": sum(c.finish_reason == "length" for c in calls),
        "failed_calls": sum(c.status != 200 for c in calls),
    }


def _effective_latency(cand: Sequence[Call], ref: Sequence[Call], items: Sequence[WorkloadItem],
                       out: Mapping[str, str]) -> tuple[list[float], float, float]:
    """Latencies with the escape hatch charged. Returns (latencies, extra_usd, retry_rate)."""

    from wattnot.gates.fixed import item_checks, run_check

    by_key = {f"{i.id}#{k}": i for k in range(len({c.repeat for c in cand}))
              for i in items}
    ref_by = {(c.task_id, c.repeat): c for c in ref}
    lats, extra, retries = [], 0.0, 0
    for c in cand:
        item = by_key[f"{c.task_id}#{c.repeat}"]
        text = out.get(f"{c.task_id}#{c.repeat}")
        ok = text is not None and all(run_check(k, text) for k in item_checks(item))
        lat = c.latency_s
        if not ok:
            r = ref_by[(c.task_id, c.repeat)]
            lat += r.latency_s
            extra += r.usd
            retries += 1
        lats.append(lat)
    return lats, extra, (retries / len(cand) if cand else 0.0)


def tune_class(name: str, items: Sequence[WorkloadItem], runner: Runner, order: Sequence[str], *,
               repeats: int = 2, max_drop_pts: float = 1.0, p99_slack: float = 0.10,
               judge: Callable | None = None) -> dict:
    witems = _expand(items, repeats)
    try:
        ref = [runner.call(i, REFERENCE, r) for r in range(repeats) for i in items]
    except BudgetStop as e:
        # not even the reference finished, so there is nothing to compare against
        zero = totals([])
        return {"class": name, "items": len(items), "repeats": repeats,
                "chosen_effort": REFERENCE, "kept": False, "tried": [], "stopped": str(e),
                "escape_retry_rate": 0.0, "measured": {**zero, "usd_with_escape": 0.0},
                "baseline_xhigh": zero, "saving_usd": 0.0, "saving_pct": 0.0,
                "cached_share": 0.0}
    ref_out = _outputs(ref)
    ref_p99 = percentile([c.latency_s for c in ref], 99)
    tried: list[dict] = []
    best: str | None = None
    best_calls: list[Call] = []
    best_extra = 0.0
    best_retry = 0.0
    stopped = None
    for effort in order:
        if best is not None and RANK[effort] > RANK[best]:
            tried.append({"effort": effort, "skipped": True,
                          "reason": f"{best} already passed; a higher level cannot be cheaper"})
            continue
        try:
            calls = [runner.call(i, effort, r) for r in range(repeats) for i in items]
        except BudgetStop as e:
            stopped = str(e)
            break
        out = _outputs(calls)
        quality = fixed_checks(ref_out, out, witems, max_drop_pts=max_drop_pts)
        lats, extra_usd, retry_rate = _effective_latency(calls, ref, items, out)
        p99 = p99_gate(Lat(ref_p99), Lat(percentile(lats, 99)), slack=p99_slack)
        results = [quality, p99]
        if judge is not None:
            results.append(judge(name, effort, ref_out, out, items))
        passed = all(r.passed for r in results)
        t = totals(calls)
        tried.append({
            "effort": effort, "skipped": False, "passed": passed,
            "failed_gates": [r.gate for r in results if not r.passed],
            "quality_pct": quality.detail["candidate_pct"],
            "reference_pct": quality.detail["baseline_pct"],
            "p99_effective_s": round(percentile(lats, 99), 4),
            "p99_budget_s": round(p99.detail["budget_s"], 4),
            "escape_retry_rate": round(retry_rate, 4),
            "gates": [{"gate": r.gate, "passed": r.passed, "reason": r.reason} for r in results],
            "usd": t["usd"], "completion_tokens": t["completion_tokens"],
            "truncated": t["truncated"]})
        if passed and (best is None or RANK[effort] < RANK[best]):
            best, best_calls, best_extra, best_retry = effort, calls, extra_usd, retry_rate
    chosen = best or REFERENCE
    measured = totals(best_calls) if best else totals(ref)
    base = totals(ref)
    measured["usd_with_escape"] = round(measured["usd"] + (best_extra if best else 0.0), 8)
    saving = base["usd"] - measured["usd_with_escape"]
    return {
        "class": name, "items": len(items), "repeats": repeats, "chosen_effort": chosen,
        "kept": best is not None, "tried": tried, "stopped": stopped,
        "escape_retry_rate": round(best_retry, 4),
        "measured": measured, "baseline_xhigh": base,
        "saving_usd": round(saving, 8),
        "saving_pct": round(100 * saving / base["usd"], 2) if base["usd"] else 0.0,
        "cached_share": cached_share(sum(c.prompt for c in runner.calls if c.cls == name),
                                     sum(c.cached for c in runner.calls if c.cls == name)),
    }


def tune(items: Sequence[WorkloadItem], runner: Runner, *, proposal: Mapping | None = None,
         classes: Sequence[str] | None = None, repeats: int = 2, max_drop_pts: float = 1.0,
         p99_slack: float = 0.10, judge: Callable | None = None) -> dict:
    by_class: dict[str, list[WorkloadItem]] = defaultdict(list)
    for i in items:
        by_class[i.meta.get("class", "default")].append(i)
    if classes:
        unknown = set(classes) - set(by_class)
        if unknown:
            raise ValueError(f"unknown class(es): {', '.join(sorted(unknown))}")
        by_class = {c: by_class[c] for c in classes}
    prop = proposal or {"orders": {n: ef.default_order() for n in by_class},
                        "proposer": "fallback", "note": "", "tokens": {"input": 0, "output": 0},
                        "usd": 0.0}
    out, stopped = [], None
    for name, its in by_class.items():
        res = tune_class(name, its, runner, prop["orders"][name], repeats=repeats,
                         max_drop_pts=max_drop_pts, p99_slack=p99_slack, judge=judge)
        out.append(res)
        if res["stopped"]:
            stopped = res["stopped"]
            break
    prop = dict(prop)
    first = prop.pop("_call", None)  # the planner call is metered too, so it is in the call log
    return {"classes": out, "proposal": prop, "stopped": stopped,
            "volatile_in_prefix": sorted(runner.volatile),
            "experiment_usd": round(runner.spent + prop.get("usd", 0.0), 8),
            "calls": ([first] if first else []) + runner.calls}
