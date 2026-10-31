#!/usr/bin/env python3
"""Gap test 3b: fully live Code Mode vs a normal tool-calling agent, on Muse Spark.

Real, harmless tools only: read_file / list_files inside a temp folder, grep in that folder,
and http_get to ONE allowlisted public no-auth host (jsonplaceholder.typicode.com, read-only GET).
Code Mode: the model writes one Python program that calls `tools.*`; it runs in a subprocess
in the temp folder with an audit hook that refuses writes outside the folder, any subprocess /
exec of other programs, and any socket connect not made by http_get to the allowlisted host.
The child gets a minimal env (no keys). Round trips, tokens (API usage) and success are MEASURED.
"""
import json, os, random, re, shutil, subprocess, sys, tempfile, textwrap, time
from datetime import UTC, datetime
from common import OUT, JobClient

MODEL = "muse-spark-1.3"
HOST = "jsonplaceholder.typicode.com"
FILES = {
    "logs/app-1.log": "INFO start\nERROR db timeout\nINFO retry\nERROR db timeout\n",
    "logs/app-2.log": "INFO start\nINFO ok\n",
    "logs/app-3.log": "ERROR disk full\nINFO cleanup\nERROR disk full\nERROR disk full\n",
    "logs/app-4.log": "WARN slow\nERROR auth failed\n",
    "config.json": json.dumps({"service": "billing", "port": 8443, "replicas": 3}),
    "requirements.txt": "requests==2.32.3\nnumpy==2.1.0\nvllm==0.32.0\n",
    "src/a.py": "# TODO: fix retry\nx = 1\n",
    "src/b.py": "# billing core\n# TODO: cache\n# TODO: metrics\n# TODO: tests\n",
    "src/c.py": "# TODO: docs\n",
}
TASKS = [
    {"id": "log-errors", "q": "Count the lines containing ERROR in each file under logs/. Which file has the most, and what is the total across all files?",
     "check": lambda a: "app-3" in a and re.search(r"\b6\b", a)},
    {"id": "config-check", "q": "Read config.json and requirements.txt. What port does the service use, and which vllm version is pinned?",
     "check": lambda a: "8443" in a and "0.32.0" in a},
    {"id": "todo-max", "q": "Search the src/ folder for TODO. Which file has the most TODOs, and what is that file's first line?",
     "check": lambda a: "b.py" in a and "billing core" in a.lower()},
    {"id": "http-user", "q": f"Using http_get on https://{HOST}, find user id 3's name and how many posts that user has (/posts?userId=3).",
     "check": lambda a: "clementine bauch" in a.lower() and re.search(r"\b10\b", a)},
]
TOOLS_DOC = """tools.list_files(dir=".") -> list[str]  (relative paths, recursive)
tools.read_file(path) -> str
tools.grep(pattern, dir=".") -> list[[path, line_no, line]]  (regex)
tools.http_get(url) -> str  (only https://jsonplaceholder.typicode.com/... allowed)"""

CHILD_TOOLS = textwrap.dedent(r'''
import os, re, sys, urllib.request, urllib.parse, threading
ROOT = os.path.realpath(os.getcwd())
HOST = %r
_net = threading.local()
def _inside(p):
    p = os.path.realpath(p); return p == ROOT or p.startswith(ROOT + os.sep)
def _hook(ev, args):
    if ev == "open":
        path, mode = args[0], args[1] if len(args) > 1 else "r"
        if isinstance(mode, str) and any(c in mode for c in "wax+") and isinstance(path, (str, bytes)) and not _inside(os.fsdecode(path)):
            raise PermissionError("write outside sandbox refused")
    elif ev in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork"):
        raise PermissionError(ev + " refused")
    elif ev == "socket.connect" and not getattr(_net, "ok", False):
        raise PermissionError("network refused (use tools.http_get)")
    elif ev in ("os.remove", "os.rename", "shutil.rmtree", "os.rmdir") and not _inside(os.fsdecode(args[0])):
        raise PermissionError(ev + " outside sandbox refused")
class tools:
    calls = 0
    @staticmethod
    def list_files(dir="."):
        tools.calls += 1; base = os.path.join(ROOT, dir); out = []
        for r, _, fs in os.walk(base):
            for f in fs:
                rel = os.path.relpath(os.path.join(r, f), ROOT)
                if not rel.startswith("_"): out.append(rel)
        return sorted(out)
    @staticmethod
    def read_file(path):
        tools.calls += 1; p = os.path.join(ROOT, path)
        if not _inside(p): raise PermissionError("outside sandbox")
        return open(p).read()
    @staticmethod
    def grep(pattern, dir="."):
        tools.calls += 1; rx = re.compile(pattern); out = []
        for rel in tools.list_files(dir):
            tools.calls -= 0
            for i, line in enumerate(open(os.path.join(ROOT, rel)), 1):
                if rx.search(line): out.append([rel, i, line.rstrip()])
        tools.calls -= 1 if False else 0
        return out
    @staticmethod
    def http_get(url):
        tools.calls += 1; u = urllib.parse.urlparse(url)
        if u.scheme != "https" or u.hostname != HOST: raise PermissionError("host not allowed")
        _net.ok = True
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=20) as r:
                return r.read(200000).decode()
        finally:
            _net.ok = False
sys.addaudithook(_hook)
''') % HOST

TOOL_SPECS = [
    {"type": "function", "function": {"name": "list_files", "description": "List files (recursive, relative paths)",
     "parameters": {"type": "object", "properties": {"dir": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read one file",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "grep", "description": "Regex search; returns [path, line_no, line]",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "dir": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "http_get", "description": f"HTTP GET, only https://{HOST}/...",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
]


def make_dir():
    d = tempfile.mkdtemp(prefix="cm-live-")
    for rel, txt in FILES.items():
        p = os.path.join(d, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(txt)
    open(os.path.join(d, "_tools.py"), "w").write(CHILD_TOOLS)
    return d


def run_child(d, src, timeout=60):
    prog = "from _tools import tools\nimport json\n" + src
    open(os.path.join(d, "_prog.py"), "w").write(prog)
    t = time.monotonic()
    p = subprocess.run([sys.executable, "-I", "-c",
                        "import sys; sys.path.insert(0, '.'); import _tools; "
                        "exec(compile(open('_prog.py').read(), '_prog.py', 'exec'), {'__name__': '__main__'})"],
                       cwd=d, env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0"},
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout[-4000:], p.stderr[-2000:], round(time.monotonic() - t, 3)


def child_tool(d, name, args):
    """Normal-agent arm: run ONE tool call in the same locked child, return its output."""
    src = f"print(json.dumps(tools.{name}(**{json.dumps(args)!s})))"
    rc, out, err, _ = run_child(d, src)
    return out.strip() if rc == 0 else f"ERROR: {err.strip()[-300:]}"


def usage_add(acc, r):
    u = r.get("usage") or {}
    acc["prompt"] += u.get("prompt_tokens", 0); acc["completion"] += u.get("completion_tokens", 0)
    acc["calls"] += 1


def arm_normal(job, task, d):
    acc = {"prompt": 0, "completion": 0, "calls": 0, "tool_calls": 0}
    msgs = [{"role": "system", "content": "You are an agent with tools over a local folder and one public read-only API. Use the tools, then give a short final answer."},
            {"role": "user", "content": task["q"]}]
    for _ in range(15):
        r = job({"model": MODEL, "reasoning_effort": "low", "max_tokens": 4000, "temperature": 0,
                 "tools": TOOL_SPECS, "messages": msgs}, purpose="gap-1031-codemode-live-normal")
        usage_add(acc, r)
        m = r["choices"][0]["message"]
        tcs = m.get("tool_calls") or []
        msgs.append({"role": "assistant", "content": m.get("content"), **({"tool_calls": tcs} if tcs else {})})
        if not tcs:
            return m.get("content") or "", acc
        for tc in tcs:
            fn = tc["function"]; acc["tool_calls"] += 1
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": child_tool(d, fn["name"], args)[:6000]})
    return "[hit 15-turn cap]", acc


def arm_codemode(job, task, d):
    acc = {"prompt": 0, "completion": 0, "calls": 0, "tool_calls": 0, "program_runs": 0}
    msgs = [{"role": "system", "content": "Write ONE Python 3 program that solves the task by calling this API "
             "(already imported as `tools`):\n" + TOOLS_DOC +
             "\nPrint the final answer in plain words as the last line. Reply with only the code in one ```python block."},
            {"role": "user", "content": task["q"]}]
    out = ""
    for attempt in range(2):  # one fix-up allowed if the program crashes
        r = job({"model": MODEL, "reasoning_effort": "low", "max_tokens": 4000, "temperature": 0,
                 "messages": msgs}, purpose="gap-1031-codemode-live-cm")
        usage_add(acc, r)
        text = r["choices"][0]["message"].get("content") or ""
        m = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
        src = m.group(1) if m else text
        rc, out, err, wall = run_child(d, src)
        acc["program_runs"] += 1; acc["sandbox_wall_s"] = wall
        if rc == 0:
            return out.strip(), acc
        msgs += [{"role": "assistant", "content": text},
                 {"role": "user", "content": f"The program failed:\n{err[-1500:]}\nFix it; reply with only the code."}]
    return "[program failed] " + out, acc


def main():
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    job = JobClient(); s0 = job.own()
    # sandbox self-check: the guards must actually refuse (MEASURED)
    d = make_dir()
    probes = {"write_outside": "open('/tmp/_cm_escape_test','w').write('x')",
              "subprocess": "import subprocess; subprocess.run(['true'])",
              "raw_socket": "import socket; socket.create_connection(('example.com', 80), 3)",
              "other_host": "tools.http_get('https://example.com/')",
              "allowed_get": "print(len(tools.http_get('https://%s/users/3')) > 0)" % HOST}
    guard = {k: run_child(d, v)[0] for k, v in probes.items()}
    guard = {k: ("refused" if rc else "allowed") for k, rc in guard.items()}
    shutil.rmtree(d)
    print("guard", guard, flush=True)
    rows = []
    jobs = [(t, arm, rep) for rep in range(reps) for t in TASKS for arm in ("normal", "codemode")]
    random.Random(7).shuffle(jobs)
    for t, arm, rep in jobs:
        d = make_dir(); t0 = time.monotonic()
        try:
            ans, acc = (arm_normal if arm == "normal" else arm_codemode)(job, t, d)
            err = None
        except Exception as e:
            ans, acc, err = "", {"prompt": 0, "completion": 0, "calls": 0}, f"{type(e).__name__}: {e}"[:200]
        shutil.rmtree(d, ignore_errors=True)
        row = {"task": t["id"], "arm": arm, "rep": rep, "ok": bool(t["check"](ans)) if not err else False,
               "answer": ans[-300:], "round_trips": acc["calls"], "prompt_tokens": acc["prompt"],
               "completion_tokens": acc["completion"], "tool_calls": acc.get("tool_calls"),
               "wall_s": round(time.monotonic() - t0, 2), "error": err}
        rows.append(row); print(json.dumps(row)[:300], flush=True)
    def agg(arm):
        rs = [r for r in rows if r["arm"] == arm]; n = len(rs)
        return {"runs": n, "passed": sum(r["ok"] for r in rs),
                "round_trips_per_run": round(sum(r["round_trips"] for r in rs) / n, 2),
                "tokens_per_run": round(sum(r["prompt_tokens"] + r["completion_tokens"] for r in rs) / n, 1),
                "prompt_tokens_per_run": round(sum(r["prompt_tokens"] for r in rs) / n, 1),
                "completion_tokens_per_run": round(sum(r["completion_tokens"] for r in rs) / n, 1),
                "wall_s_per_run": round(sum(r["wall_s"] for r in rs) / n, 2), "label": "MEASURED"}
    res = {"spec": "wattnot.gap-1031.codemode-live/1", "generated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "model": MODEL, "tools": "real: temp-folder reads, local grep, public GET to " + HOST,
           "sandbox_guard_selfcheck": guard, "arms": {a: agg(a) for a in ("normal", "codemode")},
           "rows": rows, "spend_usd": round(job.own() - s0, 4), "spend_label": "MEASURED (Meta spend ledger)"}
    (OUT / "codemode").mkdir(parents=True, exist_ok=True)
    (OUT / "codemode" / "live.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k] for k in ("sandbox_guard_selfcheck", "arms", "spend_usd")}, indent=1))

if __name__ == "__main__":
    main()
