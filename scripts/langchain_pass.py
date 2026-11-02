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
import os
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


def run_agent(base_url: str, model: str, rows: list[dict], max_steps: int, framework_tag: str,
              kind: str = "math", on_item=None):
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
                                                       "X-WattNot-Run": f"{framework_tag}-{i}",
                                                       "X-WattNot-Kind": kind}).bind_tools(tl)
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
        # final text kept (trimmed) so a later CPU test can REPLAY this round's answers
        out.append({"i": i, "steps": steps, "correct": bool(r["ok"](r["item"], text)),
                    "final_text": (text or "")[-1500:]})
        if on_item:   # per-item progress: a run cut mid-block still returns the scores so far
            on_item(out[-1])
    return out


def progress_writer(out_dir: Path, arm: str):
    """Append each finished item to <out>/langchain_items.jsonl (flushed + fsynced). The Modal
    container copies <out> to the results volume every 30 s, so a cut run keeps these lines
    (r04e LangChain-only run, 13:18 IST: the client disconnect lost all 17 min of answers)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "langchain_items.jsonl"

    def write(row: dict):
        with path.open("a") as f:
            f.write(json.dumps({"arm": arm, "t": round(time.time(), 1), **row}, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
    return write


def partial_scores(out_dir: Path) -> dict:
    """Scores from langchain_items.jsonl (for a cut run): correct / done per arm."""
    path = Path(out_dir) / "langchain_items.jsonl"
    arms: dict = {}
    for ln in path.read_text().splitlines() if path.exists() else []:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        a = arms.setdefault(r["arm"], {"correct": 0, "done": 0})
        a["done"] += 1
        a["correct"] += bool(r.get("correct"))
    return arms


def serve(upstream, saver_on: bool, label: str, tiers, final_check: bool = False, max_stepups: int = 1,
          short_bypass_tokens: int | None = None):
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
                     tiers=tiers if saver_on else None, label=label,
                     final_check=final_check and saver_on, max_stepups=max_stepups,
                     short_bypass_tokens=short_bypass_tokens)
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
    ap.add_argument("--route", choices=("gate", "final-check"), default="gate",
                    help="saver-on arm routing. final-check judges only the agent's FINAL answer "
                         "(tool-call turns are never a pass) and steps a shaky one up to thinking on "
                         "(r04c Modal: 'gate' passed every tool turn, 70/71 calls thinking off, 11/20 vs 19/20)")
    ap.add_argument("--max-stepups", type=int, default=1, help="step-up cap per agent run (final-check)")
    ap.add_argument("--short-bypass-tokens", type=int, default=None,
                    help="agent calls under this many tokens pass through untouched (no done tool, "
                         "final check only on an empty reply). Default: the saver's gate (500); 0 = off")
    ap.add_argument("--fake", action="store_true", help="local mock upstream, MOCK numbers")
    ap.add_argument("--provider", choices=("vllm", "baseten"), default="vllm",
                    help="vllm = our own GPU server (--base-url); baseten = Baseten Model API on "
                         "BASETEN_METRICS_KEY, effort routing low -> medium, token energy ESTIMATE")
    ap.add_argument("--model", default=None,
                    help="model slug (baseten default meta-models/muse-spark-open)")
    ap.add_argument("--baseten-url", default=None, help="override Baseten base URL (mock tests)")
    ap.add_argument("--hard-usd", type=float, default=2.0, help="Baseten spend hard stop")
    ap.add_argument("--ledger", default=None, help="Baseten spend ledger (default <out>/baseten_spend.ledger.jsonl)")
    ap.add_argument("--baseline-effort", default="medium",
                    help="effort for the saver-off arm on Baseten (the plain caller, as in the Baseten round)")
    a = ap.parse_args(argv)
    from wattnot.agentwaste.frameworks import mock_upstream
    from wattnot.agentwaste.server import http_upstream

    bt = None
    if a.provider == "baseten" and not a.fake:
        from wattnot.agentwaste import baseten_upstream as B
        from wattnot.cloud import MODEL as SPARK
        from wattnot.cloud.transport import read_key   # refuses the dead BASETEN_API_KEY
        model = a.model or SPARK
        guard = B.make_guard(a.ledger or Path(a.out) / "baseten_spend.ledger.jsonl", a.hard_usd)
        bt = B.BasetenUpstream(read_key(), guard, model=model, base_url=a.baseten_url,
                               baseline_effort=a.baseline_effort)
        upstream, tiers, live = bt, B.tiers(model), True
        label = "MEASURED" if not a.baseten_url else "MOCK"
    else:
        live = bool(a.base_url) and not a.fake
        label = "MEASURED" if live else "MOCK"
        # http_upstream appends /v1/chat/completions itself; a base URL ending in /v1 gave /v1/v1 (502 on Modal)
        upstream = http_upstream(upstream_base(a.base_url)) if live else mock_upstream
        model = (a.model or GLIMMER) if live else "mock-model"
        tiers = [{"name": "glimmer-think-off", "model": model, "body_extra": {"chat_template_kwargs": {"enable_thinking": False}}},
                 {"name": "glimmer-think-on", "model": model, "reasoning": True}]
    rows = items(a.dataset, a.start, a.n)
    nv = Nvml() if (live and bt is None) else None
    res = {"spec": "wattnot.langchain-pass/1", "label": label, "dataset": a.dataset,
           "start": a.start, "n": len(rows), "framework": "langchain", "provider": a.provider,
           "model": model,
           "routing": "effort routing on one served model (thinking off -> on); model routing "
                      "needs a second model and is in the multi-provider block", "route": a.route, "arms": {}}
    for arm, on in (("saver_off", False), ("saver_on", True)):
        base, server = serve(upstream, on, label, tiers, a.route == "final-check", a.max_stepups,
                             a.short_bypass_tokens)
        if bt is not None:
            bt.arm = arm
        try:
            e0, t0 = (nv.mj() if nv else None), time.time()
            try:
                per = run_agent(base, model, rows, a.max_steps, f"lc-{arm}",
                                "code" if a.dataset == "humaneval164" else "math",
                                on_item=progress_writer(Path(a.out), arm))
            except Exception as e:  # noqa: BLE001 - Baseten stop: keep what finished, end cleanly
                if bt is None or not bt.stopped:
                    raise
                per = [json.loads(ln) for ln in (Path(a.out) / "langchain_items.jsonl").read_text().splitlines()
                       if ln.strip() and json.loads(ln).get("arm") == arm]
                res["stopped"] = f"{bt.stopped} ({type(e).__name__})"
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
        if bt is not None:
            res["arms"][arm]["baseten"] = bt.arm_summary(arm)
            if bt.stopped:
                res["stopped"] = bt.stopped
                break
    if bt is not None:
        res["routing"] = "effort routing on Baseten Spark: reasoning_effort low, step-up to medium"
        res["energy_label"] = "ESTIMATE (from tokens); no GPU energy on a hosted API"
        res["spend_usd"] = round(bt.guard.committed(), 6)
        res["hard_usd"] = a.hard_usd
        if "saver_on" not in res["arms"]:
            Path(a.out).mkdir(parents=True, exist_ok=True)
            (Path(a.out) / "langchain_pass.json").write_text(json.dumps(res, indent=2, default=str))
            print(json.dumps({"stopped": res.get("stopped"), "spend_usd": res["spend_usd"]}))
            return 6
    off, on = res["arms"]["saver_off"], res["arms"]["saver_on"]
    if off["gpu_j"] is not None and on["gpu_j"] is not None:
        res["gpu_j_saved"] = off["gpu_j"] - on["gpu_j"]
    res["quality_held"] = on["correct"] >= off["correct"] - max(1, round(0.01 * len(rows)))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "langchain_pass.json").write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps({k: v for k, v in res.items() if k != "arms"} | {
        a_: {k: v for k, v in d.items() if k in ("correct", "n", "gpu_j", "steps")}
        for a_, d in res["arms"].items()} | ({"baseten": {a_: d.get("baseten") for a_, d in res["arms"].items()}}
                                            if bt is not None else {}), default=str))
    return 0 if res["quality_held"] else 5   # a saver that loses answers is a failed block


if __name__ == "__main__":
    raise SystemExit(main())
