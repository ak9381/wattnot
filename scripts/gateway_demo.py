"""Send ~60 mixed prompts through a running WattNot gateway with the plain OpenAI SDK.

    python scripts/gateway_demo.py --base http://127.0.0.1:8770/gw/v1 [--n 60]

The gateway holds the Meta key; this script only changes base_url. It prints the gateway's own
savings summary at the end (labels included) and never prints keys.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import time
import urllib.request

from openai import OpenAI

EASY = ["What is 17 + 26?", "Capital of Kenya?", "What is 12 * 11?", "Spell 'necessary' backwards.",
        "Is 91 prime? Answer yes or no.", "How many minutes in 3.5 hours?", "What is 15% of 240?",
        "Name the largest planet.", "Convert 5 km to metres.", "What is the square root of 196?",
        "What day comes after Thursday?", "What is 2 to the power 10?", "Translate 'thank you' to Spanish.",
        "What is 1000 - 387?", "Boiling point of water in Celsius at sea level?"]
HARD = ["A train leaves at 9:40 and arrives at 13:05. It stopped 3 times for 7 minutes each. How many minutes was it moving? Give the number.",
        "Find the smallest positive integer n so that n mod 3 = 2, n mod 5 = 3 and n mod 7 = 2. Give the number.",
        "A shop raises a price by 20% then cuts it by 20%. The final price is 96. What was the start price? Give the number.",
        "Write a Python function that returns the n-th Fibonacci number iteratively. Code only.",
        "Two pipes fill a tank in 6 h and 9 h. A drain empties it in 12 h. All open: how many hours to fill? Give the number."]
LONG = ("Meeting notes, platform team. " + " ".join(
    f"Item {i}: the team discussed service {i} latency, owners, next steps and the rollout window for region {i % 5}."
    for i in range(60)))


def loop_body(model: str) -> dict:
    call = {"id": "c1", "type": "function", "function": {"name": "search", "arguments": json.dumps({"q": "rack power limits"})}}
    msgs = [{"role": "user", "content": "Find the H100 default power limit, then answer in one line."}]
    for i in range(4):
        msgs += [{"role": "assistant", "content": None, "tool_calls": [dict(call, id=f"c{i}")]},
                 {"role": "tool", "tool_call_id": f"c{i}", "content": "H100 SXM default power limit: 700 W."}]
    return {"model": model, "messages": msgs,
            "tools": [{"type": "function", "function": {"name": "search", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}]}


def image_uri() -> str:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (1800, 1200), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([200, 200, 1600, 1000], outline="black", width=12)
    d.text((300, 500), "INVOICE TOTAL 4,250", fill="black")
    b = io.BytesIO()
    im.save(b, format="PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def plan(n: int) -> list[tuple[str, dict]]:
    img = image_uri()
    items = []
    for i in range(n):
        k = i % 12
        if k in (0, 1, 2, 3, 4, 5, 6):
            items.append(("easy", {"messages": [{"role": "user", "content": EASY[i % len(EASY)]}]}))
        elif k in (7, 8):
            items.append(("hard", {"messages": [{"role": "user", "content": HARD[i % len(HARD)]}]}))
        elif k == 9:
            items.append(("agent-loop", loop_body("x")))
        elif k == 10:
            items.append(("long-context", {"messages": [{"role": "system", "content": LONG},
                                                        {"role": "user", "content": "Which region is item 42 in? Number only."}]}))
        else:
            items.append(("image", {"messages": [{"role": "user", "content": [
                {"type": "text", "text": "What total is written in this image? Number only."},
                {"type": "image_url", "image_url": {"url": img}}]}]}))
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8770/gw/v1")
    ap.add_argument("--n", type=int, default=60)
    a = ap.parse_args()
    client = OpenAI(base_url=a.base, api_key="unused-the-gateway-holds-the-key")
    kinds: dict[str, int] = {}
    errors = 0
    t0 = time.time()
    for i, (kind, body) in enumerate(plan(a.n)):
        body = dict(body)
        body.pop("model", None)
        try:
            r = client.chat.completions.with_raw_response.create(model="muse-spark-1.3", **body,
                                                                  extra_headers={"x-wattnot-run": f"demo-{i}"})
            rid = r.headers.get("x-wattnot-receipt")
            msg = r.parse().choices[0].message.content or ""
            print(f"{i + 1:>2} {kind:<12} {rid} {msg.strip()[:60]!r}")
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"{i + 1:>2} {kind:<12} ERROR {type(e).__name__}: {str(e)[:120]}")
        kinds[kind] = kinds.get(kind, 0) + 1
    s = json.load(urllib.request.urlopen(a.base.rstrip("/") + "/wattnot/savings"))
    print(json.dumps({"sent": kinds, "errors": errors, "seconds": round(time.time() - t0, 1),
                      "gateway_summary": {k: v for k, v in s.items() if k != "last"}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
