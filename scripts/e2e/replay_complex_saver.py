"""CPU replay of r04e's complex agent items through the with-WattNot request levers.

r04e lost complex-00/01/03/04/05/07/09/10: the live summariser compressed the task itself away and
one chunk store was shared across items. Glimmer is not available on CPU, so a scripted agent
stands in for it. It acts only on the messages the saver hands it (exactly what the server would
see): it needs the "Task:" line to know what to do (else it says "No current user request", the
r04e symptom) and the RESULT values still visible to compute. A lossy mock summariser stands in
for Muse Spark: it drops numbers and the task wording.

Two agent styles: ``notes`` writes a one-line running state into each reply (as a reasoning model
does in its visible text); ``stateless`` relies only on visible RESULT lines (the harshest case).
Arms: ``old`` = the r04e saver (main 175509e), ``new`` = the fixed saver, ``off`` = no saver.

    python scripts/e2e/replay_complex_saver.py --json out.json        # free, CPU, no network
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src")]

from wattnot.context_saver import middleware as NEW  # noqa: E402
from wattnot.e2e.fullstack import RequestLevers  # noqa: E402
from wattnot.longrun.grade import grade_one  # noqa: E402
from wattnot.longrun.tasks import FX, build  # noqa: E402
from wattnot.proof.client import Reply  # noqa: E402

CATALOG = ["gpu-fan", "psu-1600w", "nvlink-bridge", "ssd-4tb", "ram-64gb", "nic-100g"]
R04E_LOST = {"complex-00", "complex-01", "complex-03", "complex-04", "complex-05",
             "complex-07", "complex-09", "complex-10"}
REVS = {"old": "175509e", "safe": "e3a0415"}   # r04e saver; safe = short turns verbatim only
ARMS = ("off", "old", "safe", "new")
STYLES = ("notes", "stateless", "verbose", "verbose-stateless")


class LossySummarizer:
    """Keeps a short gist, drops every digit and the 'Task:' wording (a bad, but plausible, summary)."""
    model = "lossy-mock"

    def __init__(self):
        self.calls = 0

    def __call__(self, text: str) -> str:
        self.calls += 1
        gist = re.sub(r"\d+(\.\d+)?", "#", text.replace("Task:", "goal"))
        return "Summary: " + " ".join(gist.split()[:10])


def old_module(rev: str):
    src = subprocess.run(["git", "show", f"{rev}:src/wattnot/context_saver/middleware.py"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    p = Path(f"/tmp/_wattnot_mw_{rev}.py")
    p.write_text(src)
    name = f"_wattnot_mw_{rev}"
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def levers(arm: str, shared: dict):
    lv = RequestLevers(tools_supported=True)
    if arm == "off":
        lv.saver = NEW.ContextSaver(middlewares=[])
        return lv
    mod = shared.setdefault("_mod" + arm, old_module(REVS[arm])) if arm in REVS else NEW
    if arm not in shared:   # one saver for the whole arm, like the r04e LEVERS singleton
        shared[arm] = mod.ContextSaver(middlewares=[mod.ToolDedupMiddleware(),
                                                    mod.MemoryCompressionMiddleware(summarize=LossySummarizer())])
    lv.saver = shared[arm]
    return lv


_CALL = re.compile(r"^\s*CALL\s+(\w+)\s*(\{.*\})?\s*$", re.M)
_NOTE = re.compile(r"^NOTE (\{.*\})$", re.M)


def visible(msgs: list[dict]) -> dict:
    """(tool, arg) -> value from CALL/RESULT pairs and NOTE lines still in the context."""
    known = {}
    # the saver's recap lists older turns as "- [ref] text" lines; read CALL/RESULT pairs there too
    flat = []
    for m in msgs:
        c = str(m.get("content") or "")
        if m.get("role") == "system" and c.startswith("Earlier context"):
            for line in c.splitlines()[1:]:
                t = re.sub(r"^- \[[^\]]*\] ", "", line)
                flat.append({"role": "user" if t.startswith("RESULT") else "assistant", "content": t})
        else:
            flat.append(m)
    msgs = flat
    for i, m in enumerate(msgs):
        c = str(m.get("content") or "")
        if m.get("role") == "assistant":
            for n in _NOTE.findall(c):
                known.update({tuple(k.split("|")): v for k, v in json.loads(n).items()})
            cm = _CALL.search(c)
            nxt = msgs[i + 1] if i + 1 < len(msgs) else {}
            r = str(nxt.get("content") or "")
            if cm and r.startswith("RESULT ") and "ERROR" not in r:
                args = json.loads(cm.group(2) or "{}")
                key = (cm.group(1), str(args.get("item") or args.get("amount") or args.get("task") or ""))
                try:
                    known[key] = float(r[7:].strip())
                except ValueError:
                    pass
    return known


class Agent:
    """Scripted stand-in for Glimmer on the complex tool world."""

    def __init__(self, style: str):
        self.style, self.saw_no_task = style, 0

    def __call__(self, msgs, max_tokens):
        task = next((t for m in reversed(msgs) if m.get("role") == "user"
                     for t in re.findall(r"Task: (.+)", str(m.get("content") or ""))), None)
        if task is None:
            self.saw_no_task += 1
            return Reply(0.1, 20, "No current user request. FINAL 0" if False else
                         "No current user request.", "stop")
        known = visible(msgs)
        items = [x for x in sorted(CATALOG, key=lambda x: task.find(x)) if x in task]
        target = float(re.search(r"to (\d+) units", task).group(1))
        sub_part = "Use a subagent" in task
        need = {}

        def say(line):
            note = "" if self.style not in ("notes", "verbose") else \
                "\nNOTE " + json.dumps({"|".join(k): v for k, v in known.items()})
            pre = "" if not self.style.startswith("verbose") else (
                "Let me think about what the task needs before acting. The goal is to work out the "
                "restock cost, so I should gather the stock level and the unit price for each item, "
                "then convert the currency if the question asks for it. I will take this one step at "
                "a time and check every tool result carefully before moving on to the next step. "
                "The price service can be flaky, so if it errors I will simply call it again.\n")
            return Reply(0.1, 30, pre + line + note, "stop")

        sub_hit = next((v for (t, a), v in known.items() if t == "subagent" and items[0] in a), None)
        if sub_hit is not None:   # read back from a CALL subagent/RESULT pair, also in the recap
            known[("subagent", items[0])] = sub_hit
        if sub_part and ("subagent", items[0]) not in known:
            # one subagent call for the first item; its answer comes back as RESULT <number>
            for i, m in enumerate(msgs):
                if m.get("role") == "assistant" and "CALL subagent" in str(m.get("content")):
                    r = str((msgs[i + 1] if i + 1 < len(msgs) else {}).get("content") or "")
                    if r.startswith("RESULT ") and "ERROR" not in r:
                        known[("subagent", items[0])] = float(r[7:])
            if ("subagent", items[0]) not in known:
                return say(f'CALL subagent {json.dumps({"task": f"Find the restock cost in USD for {items[0]} (to {int(target)} units). Report the USD total."})}')
        mine = items[1:] if sub_part else items
        for x in mine:
            for tool in ("inventory", "price"):
                if (tool, x) not in known:
                    return say(f'CALL {tool} {json.dumps({"item": x})}')
            need[x] = max(0.0, target - known[("inventory", x)]) * known[("price", x)]
        if sub_part:
            return say(f"FINAL {known[('subagent', items[0])] + sum(need.values()):.2f}")
        cur = "EUR" if "EUR" in task else ("INR" if "INR" in task else None)
        usd = (sum(need.values()) if cur == "EUR" else max(need.values())) if cur else sum(need.values())
        if cur:
            k = ("fx", str(round(usd, 2)) if usd != int(usd) else str(int(usd)))
            hit = next((v for (t, a), v in known.items() if t == "fx" and abs(float(a) - usd) < 1e-6), None)
            if hit is None:
                return say(f'CALL fx {json.dumps({"amount": usd, "to": cur})}')
            return say(f"FINAL {hit:.2f}")
        return say(f"FINAL {usd:.2f}")


class Chat:
    def __init__(self, agent, lv):
        self.agent, self.lv, self.calls = agent, lv, 0

    def __call__(self, msgs, max_tokens):
        self.calls += 1
        nb, info = self.lv.apply({"messages": msgs, "max_tokens": max_tokens})
        sv = info.get("saver") or {}
        self.tok_before = getattr(self, "tok_before", 0) + int(sv.get("tokens_before", 0) or 0)
        self.tok_after = getattr(self, "tok_after", 0) + int(sv.get("tokens_after", sv.get("tokens_before", 0)) or 0)
        if nb is None:
            return Reply(0.0, 0, "", "error")
        return self.agent(nb["messages"], max_tokens)


def run(ids: list[str], arm: str, style: str) -> list[dict]:
    tasks = {t.id: t for t in build() if t.tier == "complex"}
    shared, out = {}, []
    for i in ids:
        ag = Agent(style)
        ch = Chat(ag, levers(arm, shared))
        g = grade_one(tasks[i], ch)
        out.append({"id": i, "arm": arm, "style": style, "ok": g.ok, "final": g.detail.get("final"),
                    "answer": tasks[i].answer, "turns": g.detail.get("turns"),
                    "stopped": g.detail.get("stopped"), "no_task_replies": ag.saw_no_task,
                    "r04e_lost": i in R04E_LOST, "prompt_tokens_before": getattr(ch, "tok_before", 0),
                    "prompt_tokens_after": getattr(ch, "tok_after", 0)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default=",".join(f"complex-{i:02d}" for i in range(11)))
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)
    ids = a.ids.split(",")
    rows = [r for arm in ARMS for style in STYLES for r in run(ids, arm, style)]
    f = lambda r: "PASS" if r["ok"] else f"FAIL({r['stopped'] or r['final']})"  # noqa: E731
    for style in STYLES:
        print(f"\n[{style}] item        " + "".join(f"{x:>18}" for x in ARMS))
        for i in ids:
            cell = {r["arm"]: r for r in rows if r["id"] == i and r["style"] == style}
            print(f"  {i} " + "".join(f"{f(cell[x]):>18}" for x in ARMS) + ("   r04e lost" if i in R04E_LOST else ""))
        print("  pass / turns / prompt tokens sent (MOCK, est.):")
        for x in ARMS:
            rs = [r for r in rows if r["arm"] == x and r["style"] == style]
            b, af = sum(r["prompt_tokens_before"] for r in rs), sum(r["prompt_tokens_after"] for r in rs)
            print(f"    {x:6} {sum(r['ok'] for r in rs)}/{len(rs)}  turns {sum(r['turns'] for r in rs)}  "
                  f"tokens {b} -> {af} (saved {100 * (b - af) / b if b else 0:.1f}%)")
    if a.json:
        Path(a.json).write_text(json.dumps({"label": "MOCK (CPU replay, scripted agent, lossy mock summariser)",
                                            "rows": rows}, indent=2))
    new_ok = all(r["ok"] for r in rows if r["arm"] == "new")
    return 0 if new_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
