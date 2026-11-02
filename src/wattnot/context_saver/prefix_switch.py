"""Pick vLLM prefix caching per workload, at server launch.

vLLM sets prefix caching when the server starts, so this is a server-level choice, not a
per-request one. We look at how much prompt text the workload repeats:

- long conversations and agent loops send the same system prompt, tool list and history again
  and again, so a cache pays off -> on
- short single questions share little text, and keeping the cache cost time -> off

Evidence for the default (r05 attribution, Modal A100, 100 items, c32/s128, MEASURED): with the
cache on the hit rate was 34.5% and the run took 200 s vs 135 s; J per correct 672 vs 459.

The ratio is "share of prompt characters already seen as a prefix of an earlier prompt". It is a
cheap stand-in for vLLM's block-level hit rate and needs no tokenizer.

Override with WATTNOT_PREFIX_CACHE=on|off|auto and WATTNOT_PREFIX_CACHE_THRESHOLD=<0..1>.
"""
from __future__ import annotations

import os
from typing import Iterable

DEFAULT_THRESHOLD = 0.5   # r05 single questions hit 34.5% and lost; repeated-context workloads sit far above
MIN_PREFIX_CHARS = 64     # vLLM caches whole KV blocks (16 tokens); tiny overlaps don't count


def _common_prefix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def shared_prefix_ratio(prompts: Iterable[str]) -> float:
    """Share of all prompt characters that repeat a prefix of an earlier prompt (0..1)."""
    seen: list[str] = []
    total = shared = 0
    for p in prompts:
        p = p or ""
        total += len(p)
        best = max((_common_prefix(p, q) for q in seen), default=0)
        if best >= MIN_PREFIX_CHARS:
            shared += best
        seen.append(p)
    return shared / total if total else 0.0


def prompt_text(x) -> str:
    """Flatten a prompt (str, chat messages, or an item dict / object) into one string."""
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, (list, tuple)):
        return "\n".join(prompt_text(m) for m in x)
    if isinstance(x, dict):
        for k in ("messages", "prompt", "question", "content", "text"):
            if k in x:
                return prompt_text(x[k])
        return ""
    for k in ("messages", "prompt", "question"):
        v = getattr(x, k, None)
        if v is not None:
            return prompt_text(v)
    return ""


def choose(prompts: Iterable, *, mode: str | None = None, threshold: float | None = None,
           workload: str = "") -> dict:
    """Return the launch decision as a record for the run doc and receipt."""
    mode = (mode or os.environ.get("WATTNOT_PREFIX_CACHE") or "auto").strip().lower()
    if mode not in ("auto", "on", "off"):
        raise ValueError(f"WATTNOT_PREFIX_CACHE must be auto, on or off, not {mode!r}")
    if threshold is None:
        threshold = float(os.environ.get("WATTNOT_PREFIX_CACHE_THRESHOLD") or DEFAULT_THRESHOLD)
    if not 0 <= threshold <= 1:
        raise ValueError(f"prefix cache threshold must be 0..1, not {threshold}")
    texts = [prompt_text(p) for p in prompts]
    ratio = round(shared_prefix_ratio(texts), 4)
    if mode == "auto":
        enabled = ratio >= threshold
        reason = (f"shared-prefix ratio {ratio:.1%} {'>=' if enabled else '<'} threshold {threshold:.0%}: "
                  + ("repeated context (long chat / agent loop), cache on" if enabled
                     else "short single questions, cache off"))
    else:
        enabled = mode == "on"
        reason = f"forced {mode} by WATTNOT_PREFIX_CACHE"
    return {"enabled": enabled, "mode": mode, "threshold": threshold, "shared_prefix_ratio": ratio,
            "prompts": len(texts), "workload": workload, "reason": reason,
            "vllm_flag": "--enable-prefix-caching" if enabled else "--no-enable-prefix-caching",
            "label": "MEASURED"}


def _load(path: str) -> list:
    import json
    from pathlib import Path
    p = Path(path)
    if p.suffix == ".jsonl":
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    d = json.loads(p.read_text())
    return d if isinstance(d, list) else d.get("items") or d.get("prompts") or []


def main(argv=None) -> int:
    """CLI for shell launchers (scripts/glimmer_serve.sh): writes the record, prints the vLLM flag."""
    import argparse
    import json
    from pathlib import Path
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workload", action="append", default=[], help="jsonl/json prompt file(s)")
    ap.add_argument("--out", help="write the decision record here (json)")
    o = ap.parse_args(argv)
    prompts = [x for w in o.workload for x in _load(w)]
    d = choose(prompts, workload=",".join(Path(w).name for w in o.workload) or "none given")
    if o.out:
        Path(o.out).parent.mkdir(parents=True, exist_ok=True)
        Path(o.out).write_text(json.dumps(d, indent=1))
    print(d["vllm_flag"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
