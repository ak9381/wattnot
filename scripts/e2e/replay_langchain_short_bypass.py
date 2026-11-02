"""CPU replay of r04f-langchain with the short-conversation bypass (REPLAY / ESTIMATE, $0).

Inputs: the run's langchain_pass.json (MEASURED receipts for both arms).
1. Mechanical check (SYNTHETIC upstream): every recorded call was under the saver's 500-token gate
   (saver's own counter: tokens_before == tokens_after, ~145 per call). Short agent calls are driven
   through the fixed proxy to confirm: no done tool, no answer instruction, no extra calls; an empty
   final answer still gets its one step-up.
2. Estimate: with nothing injected, the saver-on arm sends the same requests as saver-off, so its
   calls and tokens are taken as saver-off's MEASURED totals plus the one empty-reply step-up
   (avg escalated tokens per step-up from the run). Energy = saver-off J/token x tokens (ESTIMATE).
What a replay cannot show: how the live model answers once the done tool is gone (its call count,
answer lengths and accuracy may differ), and tier 0 runs thinking off while saver-off used the
model default. Only a live run settles those.

    python scripts/e2e/replay_langchain_short_bypass.py --pass-json langchain_pass.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src")]
from fastapi.testclient import TestClient  # noqa: E402

from wattnot.agentwaste.server import create_app  # noqa: E402
from wattnot.context_saver import ContextSaver  # noqa: E402

TIERS = [{"name": "off", "model": "m", "body_extra": {"chat_template_kwargs": {"enable_thinking": False}}},
         {"name": "on", "model": "m", "reasoning": True}]
TOOLS = [{"type": "function", "function": {"name": "calculator", "description": "Evaluate math.",
                                            "parameters": {"type": "object", "properties": {}}}}]


def mechanical(n_items: int = 20) -> dict:
    seen: list[dict] = []

    def up(body, headers):
        seen.append(body)
        empty = "EMPTY" in json.dumps(body["messages"]) and len(seen) % 2 == 1
        txt = "" if empty else "17 + 25 = 42\n#### 42"
        return {"choices": [{"message": {"role": "assistant", "content": txt}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    c = TestClient(create_app(up, context_saver=ContextSaver(), tiers=TIERS, final_check=True))
    for i in range(n_items):
        q = f"Item {i}{' EMPTY' if i == 0 else ''}: Ann has 17 apples and buys 25. How many? '#### <n>'"
        c.post("/v1/chat/completions", json={"model": "m", "tools": TOOLS, "messages": [
            {"role": "user", "content": q}]}, headers={"x-wattnot-run": f"r{i}"})
    done_offered = sum(any(t["function"]["name"] == "done" for t in b.get("tools", [])) for b in seen)
    return {"label": "SYNTHETIC", "items": n_items, "upstream_calls": len(seen),
            "done_tool_offered": done_offered, "expected_calls": n_items + 1}


def estimate(p: dict) -> dict:
    off, on = p["arms"]["saver_off"], p["arms"]["saver_on"]
    ro, rn = off["proxy_receipt"], on["proxy_receipt"]
    wo, wn = ro["agent_waste"], rn["agent_waste"]
    tok_off = wo["prompt_tokens"] + wo["completion_tokens"]
    j_per_tok = off["gpu_j"] / tok_off
    fc = rn["final_check"]
    steps = max(1, fc["stepped_up"])
    per_stepup = rn["escalated_attempt_tokens"] / steps
    empty = sum(v for k, v in fc["reasons"].items() if "empty" in k)
    calls = wo["upstream_calls"] + empty
    prompt = wo["prompt_tokens"] + round(per_stepup * empty * 0.7)
    comp = wo["completion_tokens"] + round(per_stepup * empty * 0.3)
    gpu_j = j_per_tok * (prompt + comp)
    return {
        "label": "REPLAY/ESTIMATE",
        "measured_r04f": {"saver_off": {"billed_calls": wo["upstream_calls"], "prompt_tokens": wo["prompt_tokens"],
                                        "completion_tokens": wo["completion_tokens"], "gpu_kj": round(off["gpu_j"] / 1e3, 1)},
                          "saver_on": {"billed_calls": wn["upstream_calls"], "prompt_tokens": wn["prompt_tokens"],
                                       "completion_tokens": wn["completion_tokens"], "gpu_kj": round(on["gpu_j"] / 1e3, 1),
                                       "final_check_stepups": fc["stepped_up"], "tokens_saved": rn["context_saver"]["tokens_saved"]}},
        "fixed_saver_on_estimate": {"billed_calls": calls, "prompt_tokens": prompt, "completion_tokens": comp,
                                    "final_check_stepups": empty, "gpu_kj": round(gpu_j / 1e3, 1),
                                    "gpu_vs_saver_off_pct": round(100 * (gpu_j / off["gpu_j"] - 1), 1),
                                    "gpu_vs_measured_saver_on_pct": round(100 * (gpu_j / on["gpu_j"] - 1), 1),
                                    "j_per_token_from_saver_off": round(j_per_tok, 3)},
        "cannot_show": ["live model behaviour without the done tool (calls, answer length, accuracy)",
                        "tier 0 is thinking-off; saver-off used the model default",
                        "energy model is linear in tokens, fitted on one arm"],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pass-json", required=True)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    res = {"mechanical": mechanical(), "estimate": estimate(json.loads(Path(a.pass_json).read_text()))}
    txt = json.dumps(res, indent=2)
    if a.out:
        Path(a.out).write_text(txt)
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
