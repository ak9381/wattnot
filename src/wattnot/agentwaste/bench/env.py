"""A tiny deterministic ops world for the agent-waste benchmark.

No network and no randomness: the same call on the same state always returns the same text.
Tools read or change an in-memory ticket queue, a few files, a service list and a docs index.
Every state change is recorded in `World.mutations`, so a checker can tell real work from
invented work.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field


def _tool(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required or []}}}


_S = {"type": "string"}
TOOLS: list[dict] = [
    _tool("list_tickets", "List tickets. Optional filters: status, owner ('none' for unassigned), "
          "priority.", {"status": _S, "owner": _S, "priority": _S}),
    _tool("get_ticket", "Get one ticket by id.", {"id": _S}, ["id"]),
    _tool("update_ticket", "Change a ticket's status and/or owner.",
          {"id": _S, "status": _S, "owner": _S}, ["id"]),
    _tool("create_ticket", "Open a new ticket.", {"title": _S, "owner": _S}, ["title"]),
    _tool("list_files", "List files under a directory path.", {"dir": _S}, ["dir"]),
    _tool("read_file", "Read a text file.", {"path": _S}, ["path"]),
    _tool("write_file", "Create or overwrite a text file.", {"path": _S, "content": _S},
          ["path", "content"]),
    _tool("service_status", "Status of one service, or 'all' for every service.", {"name": _S},
          ["name"]),
    _tool("search_docs", "Search the docs index.", {"query": _S}, ["query"]),
    _tool("post_message", "Post a message to the operator. Use it to report results.",
          {"text": _S}, ["text"]),
]
MUTATING = {"update_ticket", "create_ticket", "write_file"}


def _tickets() -> dict[str, dict]:
    rows = [
        ("T-101", "Login page slow", "open", "sam", "medium"),
        ("T-102", "Invoice export fails", "open", "none", "high"),
        ("T-103", "Update TLS cert", "resolved", "sam", "low"),
        ("T-104", "Search index stale", "open", "kim", "high"),
        ("T-105", "Cache eviction bug", "blocked", "kim", "medium"),
        ("T-106", "Dark mode request", "open", "none", "low"),
        ("T-107", "Webhook retries", "open", "none", "high"),
        ("T-108", "Rotate old logs", "resolved", "sam", "low"),
        ("T-109", "Billing rounding", "open", "none", "high"),
        ("T-110", "Dashboard typo", "blocked", "lee", "low"),
    ]
    return {i: {"id": i, "title": t, "status": s, "owner": o, "priority": p}
            for i, t, s, o, p in rows}


FILES = {
    "/README.txt": "Ops handbook v3\nSee /config/app.yaml for limits.\n",
    "/config/app.yaml": "name: shop\nmax_connections: 120\ntimeout_s: 30\n",
    "/notes/todo.txt": "Pick up T-101 and T-104 today.\n",
    "/drafts/a.txt": "Release notes draft: faster login.\n",
    "/reports/limits.txt": "max_connections=120\n",
}
SERVICES = {"billing": "degraded", "search": "up", "auth": "up", "checkout": "down"}
DOCS = {
    "rotate api key": "To rotate the API key: 1) create a new key in the console, 2) deploy it, "
                      "3) revoke the old key.",
    "rate limits": "Default rate limit is 600 requests per minute per key.",
    "backups": "Backups run nightly at 02:00 and are kept 14 days.",
}


@dataclass
class World:
    tickets: dict[str, dict] = field(default_factory=_tickets)
    files: dict[str, str] = field(default_factory=lambda: dict(FILES))
    services: dict[str, str] = field(default_factory=lambda: dict(SERVICES))
    docs: dict[str, str] = field(default_factory=lambda: dict(DOCS))
    messages: list[str] = field(default_factory=list)
    mutations: list[tuple[str, dict]] = field(default_factory=list)

    @classmethod
    def with_changes(cls, **kw) -> World:
        w = cls()
        for k, v in kw.items():
            setattr(w, k, copy.deepcopy(v))
        return w

    # ------------------------------------------------------------------ tools
    def call(self, name: str, args: dict) -> str:
        fn = getattr(self, f"_t_{name}", None)
        if fn is None:
            return json.dumps({"error": f"unknown tool {name}"})
        try:
            return json.dumps(fn(**args), separators=(",", ":"))
        except TypeError:
            return json.dumps({"error": "bad arguments"})

    def _t_list_tickets(self, status=None, owner=None, priority=None):
        rows = [t for t in self.tickets.values()
                if (status is None or t["status"] == status)
                and (owner is None or t["owner"] == owner)
                and (priority is None or t["priority"] == priority)]
        return rows

    def _t_get_ticket(self, id):  # noqa: A002
        return self.tickets.get(id) or {"error": "no such ticket"}

    def _t_update_ticket(self, id, status=None, owner=None):  # noqa: A002
        t = self.tickets.get(id)
        if t is None:
            return {"error": "no such ticket"}
        changed = False
        for k, v in (("status", status), ("owner", owner)):
            if v is not None and t[k] != v:
                t[k] = v
                changed = True
        if changed:
            self.mutations.append(("update_ticket", {"id": id, "status": status, "owner": owner}))
        return {"ok": True, "changed": changed, "ticket": t}

    def _t_create_ticket(self, title, owner="none"):
        tid = f"T-{111 + sum(1 for m in self.mutations if m[0] == 'create_ticket')}"
        self.tickets[tid] = {"id": tid, "title": title, "status": "new", "owner": owner,
                             "priority": "medium"}
        self.mutations.append(("create_ticket", {"title": title, "owner": owner}))
        return {"ok": True, "changed": True, "id": tid}

    def _t_list_files(self, dir):  # noqa: A002
        d = dir.rstrip("/") + "/"
        return sorted(p for p in self.files if p.startswith(d))

    def _t_read_file(self, path):
        if path not in self.files:
            return {"error": "no such file"}
        return {"path": path, "content": self.files[path]}

    def _t_write_file(self, path, content):
        changed = self.files.get(path) != content
        self.files[path] = content
        if changed:
            self.mutations.append(("write_file", {"path": path, "content": content}))
        return {"ok": True, "changed": changed}

    def _t_service_status(self, name):
        if name == "all":
            return self.services
        return {name: self.services[name]} if name in self.services else {"error": "unknown"}

    def _t_search_docs(self, query):
        q = set(query.lower().split())
        return [{"title": k, "text": v} for k, v in self.docs.items() if q & set(k.split())]

    def _t_post_message(self, text):
        self.messages.append(str(text))
        return {"ok": True}
