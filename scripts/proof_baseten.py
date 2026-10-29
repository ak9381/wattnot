"""Baseten proof: 3 tiny calls through the $98 spend guard; compare ledger vs returned usage.
Key from BASETEN_METRICS_KEY (fallback BASETEN_API_KEY); never printed."""
import json, os, sys, threading, time, urllib.request, urllib.error
from pathlib import Path
from wattnot.cloud import BASE_URL, MODEL, inference_key
from wattnot.spend.guard import Guard, Ledger, Price, TOKEN, ProviderError, run_items

OUT = Path("docs/runs/proof-20261029")
SERVER_TIMEOUT_S = 120                      # per-request urllib timeout
CLIENT_KILL_S = SERVER_TIMEOUT_S + 30       # local backstop, slightly above the server timeout


def arm_kill_timer(seconds: int = CLIENT_KILL_S) -> threading.Timer:
    """Last-resort local watchdog per request: if a socket wedges past the server timeout, exit so
    the script cannot hang forever. Cancelled as soon as the request returns."""
    t = threading.Timer(seconds, lambda: os._exit(3))
    t.daemon = True
    t.start()
    return t
_, KEY = inference_key()
assert KEY, "no Baseten key"
PRICE = Price(TOKEN, 1.25, 4.25)
g = Guard("baseten", PRICE, Ledger(OUT / "baseten-proof-ledger.jsonl"), hard_usd=0.01, soft_usd=0.009,
          lag_margin_usd=0.0)
raw = []
prompts = ["What is 7*8? Answer with the number only.", "Capital of France? One word.",
           "Is 17 prime? yes or no."]

def call(it):
    body = {"model": MODEL, "reasoning_effort": "low", "max_tokens": 256,
            "messages": [{"role": "user", "content": it["prompt"]}]}
    r = urllib.request.Request(f"{BASE_URL}/chat/completions", data=json.dumps(body).encode(),
                               headers={"Authorization": f"Bearer {KEY}",
                                        "Content-Type": "application/json"})
    killer = arm_kill_timer()
    try:
        with urllib.request.urlopen(r, timeout=SERVER_TIMEOUT_S) as resp:
            d = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise ProviderError(e.code) from None
    finally:
        killer.cancel()
    u = d.get("usage") or {}
    c = d["choices"][0]["message"].get("content")
    raw.append({"id": it["id"], "usage": u, "content": c, "finish": d["choices"][0].get("finish_reason")})
    return {"tokens_in": u.get("prompt_tokens", 0), "tokens_out": u.get("completion_tokens", 0),
            "answer": c, "cut_off": not c}

worst = lambda it: PRICE.tokens(200, 256)
rec = run_items(g, [{"id": f"p{i}", "prompt": p} for i, p in enumerate(prompts)], call, worst, OUT)
(OUT / "baseten-proof-raw.json").write_text(json.dumps(raw, indent=1))
own = sum(PRICE.tokens(x["usage"].get("prompt_tokens", 0), x["usage"].get("completion_tokens", 0)) for x in raw)
print(json.dumps({"receipt": rec, "provider_usage_priced_usd": round(own, 6),
                  "raw": [{k: v for k, v in x.items()} for x in raw]}, indent=1))
