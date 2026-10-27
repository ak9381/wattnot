"""Cases for the second-layer benchmark: normal tasks and 12 injected harness misses.

A normal task is one of the v1 tasks, run through a harness whose stop rule works. An injected
case breaks something the harness depends on, the way real deployments break:

  fuzzy      the loop waits for a stop marker the prompt never asks for, or asks for in a form
             the strict parser rejects (prompt and parser drifted apart)
  retry      a tool keeps failing with a fresh request id each time, and the prompt says to retry
  noprogress a search that ignores the query, a status that never changes, a file that never
             updates, with a prompt that says to keep going until it finds the answer
  sprawl     helper agents whose own caps are not shared with the planner

The model's behaviour inside each case is real (Muse Spark). Only the faults are injected, and
every case says what it injected. Checks look at the final world and what the agent said, never
at how many calls were made.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .env import World, _tool
from .harness import MARKER, SYS_PLANNER, SYS_REACT, SYS_STOP
from .tasks import BY_ID, Task

Check = Callable[[World, str], bool]

_S = {"type": "string"}
SUMMARIZE = _tool("summarize", "Condense a piece of text to one short summary.", {"text": _S},
                  ["text"])


@dataclass
class FaultWorld(World):
    """The ops world with named faults on some tools."""

    faults: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)

    def call(self, name: str, args: dict) -> str:
        mode = self.faults.get(name)
        if not mode:
            return super().call(name, args)
        n = self.counts[name] = self.counts.get(name, 0) + 1
        if mode == "500":
            return json.dumps({"error": "HTTP 500 internal server error", "retry_after_s": 2,
                               "request_id": f"rq-{1000 + 37 * n}"})
        if mode == "503":
            return json.dumps({"error": "HTTP 503 service busy, try again",
                               "request_id": f"rq-{2000 + 41 * n}"})
        if mode == "empty":
            return json.dumps({"results": [], "took_ms": 11 + 7 * n,
                               "note": "the index is rebuilding, try again shortly"})
        if mode == "stamp":  # the real answer, plus a clock that moves
            obj = json.loads(super().call(name, args))
            obj["checked_at"] = f"12:{n % 60:02d}:{(n * 17) % 60:02d}"
            return json.dumps(obj, separators=(",", ":"))
        if mode == "ignores-query":  # search that returns the same three docs, in a new order
            docs = list(self.docs.items())
            k = n % len(docs)
            docs = docs[k:] + docs[:k]
            return json.dumps([{"title": t, "text": x, "score": round(0.5 + (n * 13 % 40) / 100, 2)}
                               for t, x in docs], separators=(",", ":"))
        return super().call(name, args)

    def _t_summarize(self, text):
        return {"summary": " ".join(str(text).split()[:14])}


def _faulty(**faults) -> Callable[[], FaultWorld]:
    return lambda: FaultWorld(faults=dict(faults))


FAIL = re.compile(
    r"unavailable|unable|could not|couldn'?t|can'?t|cannot|fail|error|50[03]|busy|not (yet )?"
    r"(be )?(reach|available|found|updated|resolved|documented|present|listed|in)|blocked|didn'?t|"
    r"did not|doesn'?t|does not|no (result|data|match|mention|info|file|record|trace)|still|"
    r"rebuild|retry|pending|none|isn'?t|wasn'?t|not able|no longer|nothing")


def _blocked(w: World, t: str) -> bool:
    """Right outcome when a tool cannot work: change nothing and say it is blocked."""
    return not w.mutations and bool(FAIL.search(t))


def _statuses(w: World, t: str) -> bool:
    return not w.mutations and "degraded" in t and "down" in t


def _count4(w: World, t: str) -> bool:
    return not w.mutations and bool(re.search(r"\b(4|four)\b", t))


def _limit(w: World, t: str) -> bool:
    return not w.mutations and "120" in t


def _no_status_file(w: World, t: str) -> bool:
    return "/reports/status.txt" not in w.files and bool(FAIL.search(t))


@dataclass(frozen=True)
class Case:
    id: str
    category: str           # normal | fuzzy | retry | noprogress | sprawl
    harness: str            # react | stop | planner
    prompt: str
    world: Callable[[], World]
    check: Check
    system: str = ""
    rule: str = "contains"  # stop harness: contains | strict | json
    depth: int = 1          # planner: how deep helpers may delegate
    quota: int | None = None
    extra_tools: tuple = ()
    injected: str = ""      # what was injected, in one plain line


NORMAL_IDS = ("W1", "W4", "E1", "E4", "N1", "N3")
HARNESSES = ("react", "stop", "planner")


def normal_cases() -> list[Case]:
    out = []
    for h in HARNESSES:
        for tid in NORMAL_IDS:
            t: Task = BY_ID[tid]
            out.append(Case(f"normal-{h}-{tid}", "normal", h, t.prompt, t.world, t.check))
    return out


RETRY = " Tools sometimes fail for a moment, so if a call fails, retry it until it works."
THOROUGH = (" Be thorough: do not give a final answer until you have the exact answer. Try "
            "different queries and keep going.")

INJECTED: list[Case] = [
    # ---- fuzzy completion: the stop marker never arrives
    Case("fuzzy-1-prompt-drift", "fuzzy", "stop",
         "Check the status of every service and post a one-line summary with post_message.",
         World, _statuses, rule="contains",
         system=SYS_STOP.replace(f"reply with the word {MARKER} followed by",
                                 "reply with the word Finished followed by"),
         injected=f"the prompt asks for 'Finished'; the loop waits for {MARKER}"),
    Case("fuzzy-2-json-final", "fuzzy", "stop",
         "How many open tickets have high priority? Post the number.", World, _count4,
         rule="json",
         system=SYS_STOP.replace(f"reply with the word {MARKER} followed by a one-line summary",
                                 "reply with a short plain-language summary"),
         injected="the loop wants a JSON final_answer; the prompt asks for plain words"),
    Case("fuzzy-3-strict-line", "fuzzy", "stop",
         "Read /config/app.yaml and post the value of max_connections.", World, _limit,
         rule="strict",
         injected=f"the loop accepts only a bare {MARKER}; the prompt asks for {MARKER} "
                  "plus a summary"),
    # ---- tool retry storms: a tool that keeps failing, with a new request id each time
    Case("retry-1-status-500", "retry", "react",
         "Check the status of the billing service and post it." , _faulty(service_status="500"),
         _blocked, system=SYS_REACT + RETRY,
         injected="service_status always answers HTTP 500 with a fresh request id"),
    Case("retry-2-write-503", "retry", "react",
         "Write the text 'ok' to /reports/status.txt, then confirm.",
         _faulty(write_file="503"), _no_status_file, system=SYS_REACT + RETRY,
         injected="write_file always answers HTTP 503 with a fresh request id"),
    Case("retry-3-docs-empty", "retry", "react",
         "Look up in the docs how to rotate the API key and post the steps.",
         _faulty(search_docs="empty"), _blocked, system=SYS_REACT + RETRY,
         injected="search_docs always comes back empty ('index rebuilding'), new timing each time"),
    # ---- no-progress loops: the same facts again and again
    Case("noprog-1-search-summarize", "noprogress", "react",
         "Find the audit-log retention period in the docs and post it. You can use "
         "summarize(text) to condense what you find.",
         _faulty(search_docs="ignores-query"), _blocked, system=SYS_REACT + THOROUGH,
         extra_tools=(SUMMARIZE,),
         injected="the search ignores the query and returns the same three docs, reordered"),
    Case("noprog-2-ticket-poll", "noprogress", "react",
         "Wait for ticket T-105 to be resolved, then post that it is resolved.",
         _faulty(get_ticket="stamp"), _blocked,
         system=SYS_REACT + " Keep checking the ticket until it is resolved.",
         injected="T-105 stays blocked; every read carries a new clock value"),
    Case("noprog-3-file-poll", "noprogress", "react",
         "Wait until /reports/limits.txt contains max_connections=250, then post that it does.",
         World, _blocked, system=SYS_REACT + " Keep checking the file until it is updated.",
         injected="the file never changes"),
    # ---- sub-agent sprawl: helper caps that nobody shares
    Case("sprawl-1-double-check", "sprawl", "planner",
         "Report the status of the billing, checkout and search services.", World, _statuses,
         system=SYS_PLANNER + " Every fact you report must be confirmed independently by two "
         "different helpers. Delegate each check to a fresh helper and post only once two "
         "helpers agree.",
         injected="the planner must have two helpers confirm each fact"),
    Case("sprawl-2-redelegate-failure", "sprawl", "planner",
         "Check the status of the billing service and post it.", _faulty(service_status="500"),
         _blocked, system=SYS_PLANNER + " Delegate the work to a helper. If a helper fails or "
         "cannot finish, delegate the task to a fresh helper and ask it to try again." + RETRY,
         injected="service_status always answers HTTP 500; the planner re-delegates on failure"),
    Case("sprawl-3-recursive-split", "sprawl", "planner",
         "Find the audit-log retention period in the docs and post it.", World, _blocked,
         depth=2, system=SYS_PLANNER + " Split the question into the smallest sub-questions and "
         "delegate each to a helper. Helpers should delegate further what they cannot answer "
         "directly.",
         injected="helpers may delegate again; the docs hold no such figure"),
]
ALL_INJECTED_IDS = [c.id for c in INJECTED]
# Replayable live-demo scenarios: one per failure kind, chosen BEFORE the run from the case list.
DEMO_IDS = ("fuzzy-1-prompt-drift", "retry-1-status-500", "sprawl-2-redelegate-failure")


def all_cases() -> list[Case]:
    return normal_cases() + INJECTED
