#!/usr/bin/env python3
"""Tiny LIVE transport probe for a chat-completions endpoint (vLLM-style).

What it is for
--------------
``wattnot.longrun.think_stream`` decides, at startup, how a server surfaces a model's chain of
thought (reasoning_content side-channel, visible ``<think>`` markers, or neither) and the whole
thinking-budget enforcer hangs off that decision. The HTTP test in
``tests/test_think_http_e2e.py`` proves the enforcer against a *faked* server. This script is the
one thing the test cannot be: a check against a *real* endpoint, to confirm the real server
actually behaves like one of the emulated modes.

It is deliberately small and cheap: 3 short prompts, a tiny ``max_tokens``, and a HARD cost cap
that aborts before sending if the configured work could exceed the cap. It changes nothing on the
server, streams nothing to disk, and prints only which channels (``reasoning_content`` /
``</think>`` / ``content``) each reply used.

It is NEVER run by the coding agent. Run it yourself, pointed at a server you already pay for:

    python scripts/think_probe_live.py --url http://127.0.0.1:8000/v1/chat/completions \
        --model muse-glimmer-30b --yes

Safety
------
- Does nothing without ``--yes`` (prints the plan and the cost ceiling, then exits 0).
- Refuses to run if ``3 * max_tokens`` would exceed ``--max-total-tokens`` (default 150).
- ``--max-tokens`` is clamped to a small ceiling; there is no way to ask for a large generation.
- Localhost by default. Point ``--url`` wherever your server lives.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

PROMPTS = [
    "What is 2+2? Put the answer on its own line.",
    "Name the capital of France. One word.",
    "Is 91 a prime number? Answer yes or no, briefly.",
]
MAX_TOKENS_CEILING = 64        # the most this tool will ever ask a server to generate per prompt


def probe_once(url: str, model: str, prompt: str, max_tokens: int, timeout_s: float) -> dict:
    """One non-streaming request. Returns which channels the reply used (no content printed)."""
    body = json.dumps({"model": model, "temperature": 0, "seed": 0, "max_tokens": max_tokens,
                       "skip_special_tokens": False,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:  # noqa: S310 - operator-supplied url
        data = json.loads(r.read())
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    used = int((data.get("usage") or {}).get("completion_tokens", 0))
    return {"reasoning_content": bool(reasoning),
            "think_open_in_content": "<think>" in content,
            "think_close_in_content": "</think>" in content,
            "content_nonempty": bool(content.strip()),
            "finish": (data.get("choices") or [{}])[0].get("finish_reason"),
            "completion_tokens": used}


def classify(flags: dict) -> str:
    if flags["reasoning_content"]:
        return "parser-split  (reasoning_content side-channel; close marker hidden)"
    if flags["think_open_in_content"] or flags["think_close_in_content"]:
        return "raw-marker    (thinking visible in content with <think>/</think>)"
    if flags["content_nonempty"]:
        return "no-think      (plain content, no reasoning channel, no markers)"
    return "empty         (no content at all)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8000/v1/chat/completions")
    ap.add_argument("--model", default="muse-glimmer-30b")
    ap.add_argument("--max-tokens", type=int, default=32)
    ap.add_argument("--max-total-tokens", type=int, default=150,
                    help="hard cost ceiling: abort if 3 prompts could generate more than this")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--yes", action="store_true", help="actually send (without this: dry run)")
    a = ap.parse_args(argv)

    max_tokens = min(a.max_tokens, MAX_TOKENS_CEILING)
    worst_case = len(PROMPTS) * max_tokens
    print(f"[probe] endpoint : {a.url}")
    print(f"[probe] model    : {a.model}")
    print(f"[probe] plan     : {len(PROMPTS)} prompts x {max_tokens} max_tokens "
          f"= <= {worst_case} completion tokens")
    print(f"[probe] cost cap : {a.max_total_tokens} tokens")

    if worst_case > a.max_total_tokens:
        print(f"[probe] ABORT: worst case {worst_case} > cap {a.max_total_tokens}. "
              f"Lower --max-tokens or raise --max-total-tokens.", file=sys.stderr)
        return 2

    if not a.yes:
        print("[probe] dry run (pass --yes to send). Nothing sent.")
        return 0

    spent = 0
    for i, prompt in enumerate(PROMPTS, 1):
        if spent + max_tokens > a.max_total_tokens:
            print(f"[probe] stopping before prompt {i}: would exceed cap {a.max_total_tokens}")
            break
        try:
            flags = probe_once(a.url, a.model, prompt, max_tokens, a.timeout)
        except Exception as e:  # noqa: BLE001 - a probe that cannot reach the server is reported
            print(f"[probe] prompt {i}: request failed ({type(e).__name__})", file=sys.stderr)
            return 3
        spent += flags["completion_tokens"]
        print(f"[probe] prompt {i}: {classify(flags)}")
        print(f"          reasoning_content={flags['reasoning_content']} "
              f"<think>={flags['think_open_in_content']} "
              f"</think>={flags['think_close_in_content']} "
              f"content={flags['content_nonempty']} "
              f"finish={flags['finish']} tokens={flags['completion_tokens']}")

    print(f"[probe] done. completion tokens actually generated: {spent} "
          f"(cap {a.max_total_tokens}).")
    print("[probe] expected on the GPU host: all three say 'parser-split' once the reasoning "
          "parser is on; a 'raw-marker' or 'no-think' verdict means the parser is off or failed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
