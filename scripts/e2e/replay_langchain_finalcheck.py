"""CPU replay (MOCK) of the r04e LangChain saver-on arm through the final-check route, old vs tuned.

The recorded run (r04e-langchain-20261102T081001Z) kept answers and the proxy receipt, not every
upstream reply, so a scripted agent replays its MEASURED shape: 20 items, 14 final-check step-ups
(13 x done() with no number, 1 x empty final), the other 6 answer directly. Finalize replies return
the recorded correct final_text. Token counts are MOCK (~4 chars/token; the thinking-on tier adds
REASONING_MOCK tokens per reply, an assumption, since thinking tokens were not logged).
``--comply`` = share of done() calls that carry the answer once the done tool asks for it
(0.0 = worst case: the model ignores the new description).

    python scripts/e2e/replay_langchain_finalcheck.py --items <langchain_items.jsonl> [--comply 0.5]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src")]
from fastapi.testclient import TestClient  # noqa: E402

REASONING_MOCK = 600
TIERS = [{"name": "off", "model": "m", "body_extra": {"chat_template_kwargs": {"enable_thinking": False}}},
         {"name": "on", "model": "m", "reasoning": True}]


def _tok(s):
    return max(1, len(s) // 4)


def _resp(content, tools=None, think=False, prompt=""):
    msg = {"role": "assistant", "content": content}
    if tools:
        msg["tool_calls"] = tools
    comp = _tok(content or "") + (REASONING_MOCK if think else 0) + (20 if tools else 0)
    return {"choices": [{"message": msg, "finish_reason": "tool_calls" if tools else "stop"}],
            "usage": {"prompt_tokens": _tok(prompt), "completion_tokens": comp}}


def run(create_app, rows, comply):
    stats = {"upstream_calls": 0, "tokens": 0, "correct": 0}
    n_done = 0
    for idx, r in enumerate(rows):
        ans = r["final_text"]
        num = re.findall(r"####\s*(\S+)", ans)[-1]
        mode = "done" if idx < 13 else ("empty" if idx == 13 else "plain")
        if mode == "done":
            n_done += 1
        complies = mode == "done" and n_done <= round(13 * comply)

        def up(body, headers):
            ms = body["messages"]
            p = json.dumps(ms)
            think = body.get("chat_template_kwargs", {}).get("enable_thinking", True) is not False
            stats["upstream_calls"] += 1
            if not any(m["role"] == "tool" for m in ms):       # first turn: call the calculator
                rr = _resp(None, [{"id": "c1", "type": "function", "function": {
                    "name": "calculator", "arguments": json.dumps({"expression": num})}}], think, p)
            elif "tools" not in body or mode == "plain" or (mode == "empty" and think):
                rr = _resp(ans, None, think, p)
            elif mode == "empty":
                rr = _resp("", None, think, p)
            else:
                done = ans if complies and "####" in json.dumps(body.get("tools")) else "Calculations completed."
                rr = _resp(None, [{"id": "d1", "type": "function", "function": {
                    "name": "done", "arguments": json.dumps({"reason": done})}}], think, p)
            u = rr["usage"]
            stats["tokens"] += u["prompt_tokens"] + u["completion_tokens"]
            return rr
        c = TestClient(create_app(up, tiers=TIERS, final_check=True))
        msgs = [{"role": "user", "content": f"Item {idx}. Reply '#### <number>'."}]
        tools = [{"type": "function", "function": {"name": "calculator", "parameters": {"type": "object"}}}]
        h = {"X-WattNot-Run": f"lc-{idx}", "X-WattNot-Kind": "math"}
        out = c.post("/v1/chat/completions", headers=h, json={"model": "m", "messages": msgs, "tools": tools}).json()
        tc = out["choices"][0]["message"]["tool_calls"][0]
        msgs += [out["choices"][0]["message"], {"role": "tool", "tool_call_id": tc["id"], "content": num}]
        out = c.post("/v1/chat/completions", headers=h, json={"model": "m", "messages": msgs, "tools": tools}).json()
        m = out["choices"][0]["message"]
        text = m.get("content") or ""
        for t in m.get("tool_calls") or []:
            text += json.loads(t["function"]["arguments"]).get("reason", "")
        got = re.findall(r"####\s*(\S+)", text)
        stats["correct"] += bool(got and got[-1] == num)
        rc = c.get("/v1/wattnot/receipt").json()
        stats.setdefault("stepped_up", 0)
        stats["stepped_up"] += rc["final_check"]["stepped_up"]
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True)
    ap.add_argument("--comply", type=float, default=0.0)
    ap.add_argument("--old", default="/tmp/oldsrv/old_server.py")
    a = ap.parse_args(argv)
    rows = [json.loads(x) for x in open(a.items) if '"saver_on"' in x]
    rows.sort(key=lambda r: r["i"])
    from wattnot.agentwaste.server import create_app as new
    spec = importlib.util.spec_from_file_location("old_server", a.old)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    res = {"label": "MOCK", "n": len(rows), "comply": a.comply,
           "old": run(old.create_app, rows, 0.0), "new": run(new, rows, a.comply)}
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
