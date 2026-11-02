#!/usr/bin/env python3
"""LangChain inside an E2E pass: a real LangChain agent (chat model + tools) sends all of its
traffic through the WattNot agent-waste proxy to the served model, saver OFF then ON.

* OFF arm: the proxy only forwards and logs (no guard limits, no context saver, no routing).
* ON arm: waste guard + context saver + routing. On one H100 there is one model, so routing is
  between two settings of the same Glimmer: thinking off first, thinking on if the answer gate
  fails. That is effort routing, not model routing, and the receipt says so.

Datasets are only the three we already run: our own set (proof-v1 math items), GSM8K (pinned
300) and HumanEval (pinned 164). Pick one with --dataset and a slice with --start/--n so the
E2E passes can spread them.

GPU energy comes from NVML on the host (MEASURED). Without a GPU, or with --fake, the upstream
is the local mock and every number is MOCK. No paid API is called.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import public_bench_ab as pb  # noqa: E402

GLIMMER = "meta-models/Muse-Glimmer-30B"
DATASETS = ("own", "gsm8k300", "humaneval164")


def items(name: str, start: int, n: int) -> list[dict]:
    if name == "own":
        gold = {}
        for line in (ROOT / "workloads/proof-v1.gold.jsonl").read_text().splitlines():
            if line.strip():
                g = json.loads(line)
                gold[g["id"]] = g["output"]
        rows = []
        for line in (ROOT / "workloads/proof-v1.jsonl").read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("kind") == "math" and "####" in gold.get(r["id"], ""):
                    rows.append({"id": r["id"], "question": r["prompt"], "answer": gold[r["id"]]})
        picked, grade = rows, "gsm8k"
    else:
        picked, grade = pb.load(name), pb.SOURCES[name].get("grader", name)
    prompt_fn, ok_fn = pb.GRADERS[grade]
    return [{"item": it, "prompt": prompt_fn(it), "ok": ok_fn} for it in picked[start:start + n]]


class Nvml:
    def __init__(self):
        self.h = None
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nv = pynvml
            self.h = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:  # noqa: BLE001 - no GPU here
            self.h = None

    def mj(self):
        return self.nv.nvmlDeviceGetTotalEnergyConsumption(self.h) if self.h else None


def tools():
    from langchain_core.tools import tool

    @tool
    def calculator(expression: str) -> str:
        """Evaluate a plain arithmetic expression, e.g. '16-3-4'."""
        import ast
        import operator as op
        ops = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
               ast.Pow: op.pow, ast.USub: op.neg, ast.Mod: op.mod, ast.FloorDiv: op.floordiv}

        def ev(n):
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
                return n.value
            if isinstance(n, ast.BinOp) and type(n.op) in ops:
                return ops[type(n.op)](ev(n.left), ev(n.right))
            if isinstance(n, ast.UnaryOp) and type(n.op) in ops:
                return ops[type(n.op)](ev(n.operand))
            raise ValueError("not arithmetic")
        try:
            return str(ev(ast.parse(expression, mode="eval").body))
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"

    @tool
    def style_guide() -> str:
        """The answer format rules for this task."""
        return ("Give the final number on the last line as '#### <number>'. For code, reply with "
                "one ```python block holding the full function.")

    return [calculator, style_guide]


def run_agent(base_url: str, model: str, rows: list[dict], max_steps: int, framework_tag: str):
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from langchain_openai import ChatOpenAI

    tl = tools()
    by_name = {t.name: t for t in tl}
    out = []
    for i, r in enumerate(rows):
        # one guard run per item: a shared run id let the step/token budget of the whole pass stop
        # items 13-20 (r04-try1: guard answered 17 of 29 calls, saver arm 2/20 correct)
        llm = ChatOpenAI(base_url=base_url, api_key="unused", model=model, max_tokens=2048,
                         timeout=600, default_headers={"X-WattNot-Framework": "langchain",
                                                       "X-WattNot-Run": f"{framework_tag}-{i}"}).bind_tools(tl)
        msgs = [SystemMessage("You are a careful agent. Use the tools when they help."),
                HumanMessage(r["prompt"])]
        text, steps = "", 0
        for steps in range(1, max_steps + 1):
            ai = llm.invoke(msgs, config={"metadata": {"item": i}})
            msgs.append(ai)
            if not getattr(ai, "tool_calls", None):
                text = ai.content if isinstance(ai.content, str) else str(ai.content)
                break
            for tc in ai.tool_calls:
                t = by_name.get(tc["name"])
                res = t.invoke(tc.get("args") or {}) if t else f"unknown tool {tc['name']}"
                msgs.append(ToolMessage(str(res), tool_call_id=tc["id"]))
        out.append({"i": i, "steps": steps, "correct": bool(r["ok"](r["item"], text))})
    return out


def serve(upstream, saver_on: bool, label: str, tiers):
    import uvicorn
    from wattnot.agentwaste.config import WasteConfig
    from wattnot.agentwaste.frameworks import free_port
    from wattnot.agentwaste.server import create_app
    from wattnot.context_saver import ContextSaver

    big = WasteConfig(max_steps=10 ** 6, max_total_tokens=10 ** 9, repeat_limit=10 ** 6,
                      noop_limit=10 ** 6, reasoning_hard_cap=10 ** 9, max_nudges=0,
                      inject_done=False)
    app = create_app(upstream, WasteConfig() if saver_on else big,
                     context_saver=ContextSaver() if saver_on else None,
                     tiers=tiers if saver_on else None, label=label)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}/v1", server


def upstream_base(url: str | None) -> str | None:
    """http_upstream appends /v1/chat/completions itself, so hand it the host without /v1.
    Handles a trailing slash too ('.../v1/' used to become '.../v' and then '/v/v1')."""
    if not url:
        return url
    u = url.rstrip("/")
    return u[:-3] if u.endswith("/v1") else u


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--dataset", choices=DATASETS, default="own")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--max-steps", type=int, default=6)
    ap.add_argument("--base-url", help="served model, e.g. http://127.0.0.1:8000/v1 (live)")
    ap.add_argument("--route", choices=("think-off-first", "think-on"), default="think-off-first",
                    help="saver-on arm tiers. think-on keeps the guard and saver but answers with "
                         "thinking on, like the off arm (r04c Modal: think-off-first answered 70 of 71 "
                         "calls without thinking, 11/20 vs 19/20, because a tool-using agent reply "
                         "always passes the answer gate)")
    ap.add_argument("--fake", action="store_true", help="local mock upstream, MOCK numbers")
    a = ap.parse_args(argv)
    from wattnot.agentwaste.frameworks import mock_upstream
    from wattnot.agentwaste.server import http_upstream

    live = bool(a.base_url) and not a.fake
    label = "MEASURED" if live else "MOCK"
    # http_upstream appends /v1/chat/completions itself; a base URL ending in /v1 gave /v1/v1 (502 on Modal)
    upstream = http_upstream(upstream_base(a.base_url)) if live else mock_upstream
    model = GLIMMER if live else "mock-model"
    tiers = [{"name": "glimmer-think-off", "model": model, "body_extra": {"chat_template_kwargs": {"enable_thinking": False}}},
             {"name": "glimmer-think-on", "model": model, "reasoning": True}]
    if a.route == "think-on":
        tiers = tiers[1:]
    rows = items(a.dataset, a.start, a.n)
    nv = Nvml() if live else None
    res = {"spec": "wattnot.langchain-pass/1", "label": label, "dataset": a.dataset,
           "start": a.start, "n": len(rows), "framework": "langchain",
           "routing": "effort routing on one served model (thinking off -> on); model routing "
                      "needs a second model and is in the multi-provider block", "route": a.route, "arms": {}}
    for arm, on in (("saver_off", False), ("saver_on", True)):
        base, server = serve(upstream, on, label, tiers)
        try:
            e0, t0 = (nv.mj() if nv else None), time.time()
            per = run_agent(base, model, rows, a.max_steps, f"lc-{arm}")
            e1, t1 = (nv.mj() if nv else None), time.time()
            import urllib.request
            with urllib.request.urlopen(base + "/wattnot/receipt", timeout=10) as r:
                proxy = json.loads(r.read())
        finally:
            server.should_exit = True
            time.sleep(0.3)
        ok = sum(p["correct"] for p in per)
        j = (e1 - e0) / 1000.0 if (e0 is not None and e1 is not None) else None
        res["arms"][arm] = {"correct": ok, "n": len(per), "wall_s": round(t1 - t0, 2),
                            "gpu_j": j, "gpu_j_label": "MEASURED" if j is not None else "not measured",
                            "j_per_correct": (j / ok) if (j and ok) else None,
                            "steps": sum(p["steps"] for p in per), "proxy_receipt": proxy,
                            "per_item": per}
    off, on = res["arms"]["saver_off"], res["arms"]["saver_on"]
    if off["gpu_j"] is not None and on["gpu_j"] is not None:
        res["gpu_j_saved"] = off["gpu_j"] - on["gpu_j"]
    res["quality_held"] = on["correct"] >= off["correct"] - max(1, round(0.01 * len(rows)))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "langchain_pass.json").write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps({k: v for k, v in res.items() if k != "arms"} | {
        a_: {k: v for k, v in d.items() if k in ("correct", "n", "gpu_j", "steps")}
        for a_, d in res["arms"].items()}, default=str))
    return 0 if res["quality_held"] else 5   # a saver that loses answers is a failed block


if __name__ == "__main__":
    raise SystemExit(main())
