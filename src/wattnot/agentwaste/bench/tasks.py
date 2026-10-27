"""24 agent tasks in three groups, each with a plain pass/fail check on the final state.

work      8 tasks that need real tool calls and real changes.
early     8 tasks that are done after one or two calls; the right move is to report and stop.
no_work   8 tasks whose queue is already empty; the right move is to change nothing and say so.

A check sees the world after the run and one lowercase string of everything the agent said
(`post_message` texts and plain replies). It never looks at how many calls were made.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .env import World

Check = Callable[[World, str], bool]


@dataclass(frozen=True)
class Task:
    id: str
    group: str
    prompt: str
    world: Callable[[], World]
    check: Check


NOTHING = re.compile(
    r"nothing|no (unassigned|unowned|resolved|new|blocked|pending|open|tickets?|services?|files?|"
    r"work|"
    r"action|changes?|issues?|items?|need|fix)|none|all clear|all (services )?(are )?up|already|"
    r"match(es)?|empty|not needed|no need|not required|up to date|in sync|correct")


def _no_change(w: World) -> bool:
    return not w.mutations


def _said(text: str, *needles: str) -> bool:
    return all(n.lower() in text for n in needles)


def _owner(w: World, tid: str) -> str:
    return w.tickets[tid]["owner"]


def _w1(w, t):
    ids = ("T-102", "T-107", "T-109")
    ok = all(_owner(w, i) == "dana" for i in ids) and _owner(w, "T-106") == "none"
    only = all(m[0] == "update_ticket" and m[1]["id"] in ids for m in w.mutations)
    return ok and only and _said(t, *ids)


def _w2(w, t):
    done = all(w.tickets[i]["status"] == "closed" for i in ("T-103", "T-108"))
    return done and len(w.mutations) == 2 and "2" in t


def _w3(w, t):
    return w.files.get("/reports/limits.txt", "").strip() == "max_connections=250"


def _w4(w, t):
    made = [m for m in w.mutations if m[0] == "create_ticket"]
    return len(made) == 1 and "checkout" in made[0][1]["title"].lower() \
        and made[0][1]["owner"] == "ops" and len(w.mutations) == 1


def _w5(w, t):
    return _said(t, "new key") or _said(t, "create a new key")


def _kim_busy() -> World:
    w = World()
    for tid in ("T-102", "T-107"):
        w.tickets[tid]["owner"] = "kim"
    return w


def _w6(w, t):
    return "kim" in t and _no_change(w)


def _w7(w, t):
    ok = all(w.tickets[i]["status"] == "in_progress" for i in ("T-101", "T-104"))
    return ok and len(w.mutations) == 2


def _w8(w, t):
    return w.files.get("/final/a.txt") == w.files["/drafts/a.txt"] and len(w.mutations) == 1


def _e(*needles: str) -> Check:
    return lambda w, t: _no_change(w) and _said(t, *needles)


def _n(w, t):
    return _no_change(w) and bool(NOTHING.search(t))


def _cfg250() -> World:
    w = World()
    w.files["/config/app.yaml"] = "name: shop\nmax_connections: 250\ntimeout_s: 30\n"
    return w


def _all_up() -> World:
    w = World()
    w.services = {k: "up" for k in w.services}
    return w


def _empty_inbox() -> World:
    return World()  # there are no files under /inbox


def _no_unassigned() -> World:
    w = World()
    for t in w.tickets.values():
        if t["owner"] == "none":
            t["owner"] = "kim"
    return w


def _no_resolved() -> World:
    w = World()
    for t in w.tickets.values():
        if t["status"] == "resolved":
            t["status"] = "closed"
    return w


def _no_blocked() -> World:
    w = World()
    for t in w.tickets.values():
        if t["status"] == "blocked":
            t["status"] = "open"
    return w


def _no_high_unowned() -> World:
    w = World()
    for t in w.tickets.values():
        if t["owner"] == "none" and t["priority"] == "high":
            t["owner"] = "lee"
    return w


TASKS: list[Task] = [
    # ---- work
    Task("W1", "work", "Assign every unassigned ticket with priority high to 'dana'. Then post a "
         "message listing the ticket ids you assigned.", World, _w1),
    Task("W2", "work", "Close every ticket owned by sam whose status is 'resolved'. Then post how "
         "many you closed.", World, _w2),
    Task("W3", "work", "Read /config/app.yaml, find max_connections, and write the line "
         "'max_connections=<value>' to /reports/limits.txt so it matches.", _cfg250, _w3),
    Task("W4", "work", "Find which service is down and open a ticket titled 'Outage: <service>' "
         "owned by 'ops'.", World, _w4),
    Task("W5", "work", "Look up in the docs how to rotate the API key and post the first step.",
         World, _w5),
    Task("W6", "work", "Work out which owner has the most open tickets and post their name. Do "
         "not change any ticket.", _kim_busy, _w6),
    Task("W7", "work", "Read /notes/todo.txt and set every ticket it mentions to status "
         "'in_progress'.", World, _w7),
    Task("W8", "work", "Copy /drafts/a.txt to /final/a.txt with identical content, then post "
         "that it is done.", World, _w8),
    # ---- early finish
    Task("E1", "early", "What is the status of the billing service? Post the answer.", World,
         _e("degraded")),
    Task("E2", "early", "Who owns ticket T-104? Post the owner's name.", World, _e("kim")),
    Task("E3", "early", "Is the file /config/app.yaml present? Post yes or no.", World, _e("yes")),
    Task("E4", "early", "What is the priority of ticket T-102? Post it.", World, _e("high")),
    Task("E5", "early", "Read /README.txt and post its first line.", World,
         _e("ops handbook v3")),
    Task("E6", "early", "Is the search service up? Post up or down.", World,
         lambda w, t: _no_change(w) and bool(re.search(r"\bup\b", t))),
    Task("E7", "early", "How many tickets are in status 'blocked'? Post the number.", World,
         _e("2")),
    Task("E8", "early", "What is the default rate limit in the docs? Post the number.", World,
         _e("600")),
    # ---- nothing to do
    Task("N1", "no_work", "Assign every unassigned ticket to 'dana'. If there are none, there is "
         "nothing to do: say so.", _no_unassigned, _n),
    Task("N2", "no_work", "Close every ticket whose status is 'resolved'. If there are none, say "
         "nothing needs closing.", _no_resolved, _n),
    Task("N3", "no_work", "Check all services and open a ticket for any that is down. If all are "
         "up, do nothing and say so.", _all_up, _n),
    Task("N4", "no_work", "Move any new files from /inbox to /archive. If /inbox is empty, there "
         "is nothing to do: say so.", _empty_inbox, _n),
    Task("N5", "no_work", "Post one message per blocked ticket. If no ticket is blocked, post "
         "'all clear'.", _no_blocked, _n),
    Task("N6", "no_work", "Escalate every high-priority ticket that has no owner by setting its "
         "status to 'escalated'. If there are none, say so.", _no_high_unowned, _n),
    Task("N7", "no_work", "Check that /reports/limits.txt matches max_connections in "
         "/config/app.yaml. Fix it only if it is wrong.", World, _n),
    Task("N8", "no_work", "Triage every ticket in status 'new'. If there are none, report that "
         "nothing needs triage.", World, _n),
]
BY_ID = {t.id: t for t in TASKS}
