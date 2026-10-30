"""Run tiers-v1 at stock and at a cap, metered per tier, and gate each tier.

    python -m wattnot.longrun.harness --cap 350 --out runs/tiers-350 [--fake] [--clock locked-mid]

Per tier and arm it records: GPU joules (NVML energy counter around that tier only), wall time,
pass rate, p99 item latency, tokens. The report turns that into, per tier:

- Wh per task (MEASURED, GPU only)
- mL of water per task (ESTIMATE: WUE 0.36 L/kWh headline, on-site cooling;
  up to 1.8 L/kWh including power-plant water, see wattnot.impact)
- $ per 1k tasks for energy (ESTIMATE: tariff 0.12 $/kWh) and for GPU time (ESTIMATE: the rental
  rate, default $5.13/h for the E2E H100)
- the gate: pass-rate drop <= 2 pts and p99 within +10% of stock, the same rules as the
  cap sweep. A tier that fails is reported "blocked", never hidden.

With --fake everything is simulated (FakeNvml + an oracle model that slips on agent tasks under
low caps) and every number is labelled MOCK.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from wattnot.impact import TARIFF_USD_PER_KWH, WUE_L_PER_KWH
from wattnot.proof.client import Reply, strip_reasoning
from wattnot.proof.session import MAX_DROP_PTS, P99_SLACK_PCT

from .grade import Graded, grade_one, last_number
from .tasks import TIERS, Task, build

# Per-model settings, picked with --model-config (a name below or a JSON file with the same keys).
# Reasoning models (Glimmer, served with --reasoning-parser) spend tokens thinking before the
# answer, so they need a bigger token budget; vLLM returns the thinking separately, and only
# `content` is graded. Concurrency is scaled down for bigger models so p99 stays comparable.
MODELS = {
    "llama-3.1-8b": {"model": "meta-llama/Llama-3.1-8B-Instruct", "max_tokens_scale": 1.0,
                     "concurrency_scale": 1.0, "reasoning": False},
    # Muse Glimmer 30B is Meta's own open-weights model (dense, BF16, reasoning).
    "muse-glimmer-30b": {"model": "meta-models/Muse-Glimmer-30B", "origin": "Meta", "max_tokens_scale": 8.0,
                         "concurrency_scale": 0.5, "reasoning": True, "max_model_len": 8192},
    "muse-glimmer-30b-fp8": {"model": "meta-models/Muse-Glimmer-30B", "origin": "Meta", "max_tokens_scale": 8.0,
                             "concurrency_scale": 0.5, "reasoning": True, "max_model_len": 8192,
                             "serve_args": ["--quantization", "fp8"]},
}


def model_config(name_or_path: str) -> dict:
    if name_or_path in MODELS:
        return {"name": name_or_path, **MODELS[name_or_path]}
    d = json.loads(Path(name_or_path).read_text())
    missing = {"model", "max_tokens_scale", "concurrency_scale"} - set(d)
    if missing:
        raise ValueError(f"model config needs {sorted(missing)}")
    return {"name": Path(name_or_path).stem, "reasoning": False, **d}


# vLLM prints this when the reasoning parser can't find the think tokens (seen in RUN1); then the
# thinking leaks into `content` and pass rates mean nothing.
PARSER_WARNINGS = ("Auto-initialization of reasoning token IDs failed",)


def reasoning_check(vllm_log: str, replies: int, with_reasoning: int,
                    min_share: float = 0.5) -> dict:
    """Shakedown gate for a reasoning model: the parser came up clean and most replies carry
    `reasoning_content`. Anything else fails, and the run falls back (plan: Llama) or gets fixed."""
    why = [f"vLLM log: {w!r}" for w in PARSER_WARNINGS if w in vllm_log]
    if not vllm_log:
        why.append("no vLLM log to check")
    if replies == 0:
        why.append("no replies")
    elif with_reasoning / replies < min_share:
        why.append(f"only {with_reasoning}/{replies} replies had reasoning_content")
    return {"ok": not why, "why": why or ["parser clean, reasoning_content present"],
            "replies": replies, "with_reasoning": with_reasoning}


def apply_model(tasks: list[Task], cfg: dict) -> list[Task]:
    """Scale each task's token budget for this model (a copy; the set itself never changes)."""
    from dataclasses import replace
    return [replace(t, max_tokens=int(t.max_tokens * cfg["max_tokens_scale"])) for t in tasks]


GPU_USD_PER_H = 5.13  # E2E H100 SXM incl. GST + disk at 88 INR/USD (ESTIMATE)
CONCURRENCY = {"easy": 16, "medium": 16, "hard": 8, "complex": 4}


class Chat:
    """Multi-turn OpenAI-compatible chat (vLLM), temperature 0."""

    def __init__(self, url: str, model: str, timeout_s: float = 120) -> None:
        self.url, self.model, self.timeout_s = url, model, timeout_s
        self.replies = self.reasoning_seen = 0   # for the shakedown reasoning check
        self.context_limit: int | None = None     # set from the model's max_model_len

    def __call__(self, messages: list[dict], max_tokens: int) -> Reply:
        # greedy; seed and top_k too, so results stay repeatable if temperature is ever raised
        body = json.dumps({"model": self.model, "temperature": 0, "seed": 0, "top_k": -1,
                           "max_tokens": max_tokens, "messages": messages}).encode()
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                d = json.loads(resp.read())
            ch = d["choices"][0]
            msg = ch["message"]
            if msg.get("reasoning_content") or msg.get("reasoning"):
                self.reasoning_seen += 1
            self.replies += 1
            # grade the answer, never the reasoning (pattern set in proof/client.py)
            text = strip_reasoning(msg.get("content") or "")
            return Reply(time.perf_counter() - t0, int(d.get("usage", {}).get(
                "completion_tokens", 0)), text,
                ch.get("finish_reason") or "stop")
        except Exception:  # noqa: BLE001
            return Reply(time.perf_counter() - t0, 0, "", "error")


# ---- fake model for dry runs --------------------------------------------------------------

class FakeChat:
    """Oracle that answers right, then slips on agent tasks under low caps. MOCK only."""

    def __init__(self, nvml, handle, tasks: list[Task], *, slip_below_w: int = 325,
                 step_s: float = 0.0) -> None:
        self.nvml, self.h, self.step_s = nvml, handle, step_s
        self.by_prompt = {t.prompt: t for t in tasks}
        self.slip_below_w = slip_below_w

    def _cap(self) -> int:
        return self.nvml.power_limit_mw(self.h) // 1000

    def _burn(self, tokens: int) -> float:
        cap = self._cap()
        lat = 0.002 * tokens * (1.0 + max(0, 400 - cap) / 600)
        with self.nvml.lock:
            self.nvml.energy_mj += int(min(cap * 0.96, 520) * lat * 1000)
        if self.step_s:
            time.sleep(self.step_s)
        return lat

    def __call__(self, messages: list[dict], max_tokens: int) -> Reply:
        first = messages[0]["content"]
        t = self.by_prompt.get(first)
        if t is None:                     # a sub-agent: answer its sub-task
            return self._sub(messages)
        if t.tier == "complex":
            return self._agent(t, messages)
        if t.tier == "hard":
            text = "```python\n" + _REF[t.entry] + "\n```"
            return Reply(self._burn(120), 120, text, "stop")
        ans = t.answer
        text = f"Step by step... ANSWER: {ans}" if t.tier == "medium" else str(ans)
        n = 60 if t.tier == "medium" else 4
        return Reply(self._burn(n), n, text, "stop")

    def _results(self, messages):
        return [m["content"][7:] for m in messages if m["role"] == "user"
                and m["content"].startswith("RESULT ")]

    def _agent(self, t: Task, messages) -> Reply:
        w, res = t.world, self._results(messages)
        q = t.prompt.split("Task: ")[-1]
        items = [x for x in w["inventory"] if x in q]
        items.sort(key=q.index)
        target = int(last_number(q.split(" units")[0]) or 0)
        script = []
        if t.needs_subagent:
            script.append(f'CALL subagent {{"task": "Find the USD restock cost of {items[0]} to '
                          f'{target} units. Reply FINAL <number>."}}')
            items = items[1:]
        for it in items:
            script += [f'CALL inventory {{"item": "{it}"}}', f'CALL price {{"item": "{it}"}}']
        # the flaky price tool fails once, so the oracle repeats that call
        i = sum(0 if r.startswith("ERROR 503") else 1 for r in res)
        if i < len(script):
            text = script[i]
        else:
            nums = [float(r) for r in res if not r.startswith("ERROR")]
            usd = self._usd(t, nums, target)
            cur = next((c for c in ("EUR", "INR", "GBP") if c in q), None)
            if cur and not any(m["content"].startswith("CALL fx") for m in messages
                               if m["role"] == "assistant"):
                text = f'CALL fx {{"amount": {usd}, "to": "{cur}"}}'
            else:
                final = nums[-1] if cur else usd
                if self._cap() < self.slip_below_w and hash(t.id) % 3 == 0:
                    final = round(final * 1.1, 2)   # simulated slip under a deep cap
                text = f"FINAL {final}"
        return Reply(self._burn(40), 40, text, "stop")

    @staticmethod
    def _usd(t: Task, nums: list[float], target: int) -> float:
        sub = nums[0] if t.needs_subagent else 0.0
        rest = nums[1:] if t.needs_subagent else nums
        pairs = [rest[k:k + 2] for k in range(0, len(rest) - 1, 2)]
        costs = [max(0, target - inv) * pr for inv, pr in pairs]
        if "Which costs more" in t.prompt:
            return max(costs)
        return sub + sum(costs)

    def _sub(self, messages) -> Reply:
        q = messages[0]["content"].split("Task: ")[-1]
        res = self._results(messages)
        it = q.split("cost of ")[1].split(" to ")[0]
        target = int(q.split(" to ")[1].split(" units")[0])
        script = [f'CALL inventory {{"item": "{it}"}}', f'CALL price {{"item": "{it}"}}']
        i = sum(0 if r.startswith("ERROR 503") else 1 for r in res)
        if i < 2:
            return Reply(self._burn(30), 30, script[i], "stop")
        inv, pr = [float(r) for r in res if not r.startswith("ERROR")][:2]
        return Reply(self._burn(20), 20, f"FINAL {max(0, target - inv) * pr}", "stop")


_REF = {
    "is_palindrome": "def is_palindrome(s):\n    t=[c.lower() for c in s if c.isalnum()]\n    return t==t[::-1]",
    "gcd": "def gcd(a,b):\n    while b: a,b=b,a%b\n    return a",
    "rle": "def rle(s):\n    import itertools\n    return ''.join(f'{k}{len(list(g))}' for k,g in itertools.groupby(s))",
    "flatten": "def flatten(xs):\n    out=[]\n    for x in xs:\n        out+=flatten(x) if isinstance(x,list) else [x]\n    return out",
    "second_largest": "def second_largest(xs):\n    u=sorted(set(xs))\n    return u[-2] if len(u)>1 else None",
    "word_count": "def word_count(s):\n    import re,collections\n    return dict(collections.Counter(re.findall('[a-z]+',s.lower())))",
    "roman": "def roman(n):\n    v=[(1000,'M'),(900,'CM'),(500,'D'),(400,'CD'),(100,'C'),(90,'XC'),(50,'L'),(40,'XL'),(10,'X'),(9,'IX'),(5,'V'),(4,'IV'),(1,'I')]\n    o=''\n    for k,r in v:\n        while n>=k: o+=r; n-=k\n    return o",
    "merge_intervals": "def merge_intervals(xs):\n    o=[]\n    for a,b in sorted(xs):\n        if o and a<=o[-1][1]: o[-1]=(o[-1][0],max(o[-1][1],b))\n        else: o.append((a,b))\n    return o",
    "is_prime": "def is_prime(n):\n    return n>1 and all(n%k for k in range(2,int(n**.5)+1))",
    "binary_search": "def binary_search(xs,t):\n    import bisect\n    i=bisect.bisect_left(xs,t)\n    return i if i<len(xs) and xs[i]==t else -1",
    "anagrams": "def anagrams(a,b):\n    f=lambda s: sorted(s.replace(' ','').lower())\n    return f(a)==f(b)",
    "fib": "def fib(n):\n    a,b=0,1\n    for _ in range(n): a,b=b,a+b\n    return a",
    "chunk": "def chunk(xs,n):\n    return [xs[i:i+n] for i in range(0,len(xs),n)]",
    "balanced": "def balanced(s):\n    st=[];p={')':'(',']':'[','}':'{'}\n    for c in s:\n        if c in '([{': st.append(c)\n        elif c in p:\n            if not st or st.pop()!=p[c]: return False\n    return not st",
    "dedupe": "def dedupe(xs):\n    return list(dict.fromkeys(xs))",
    "camel_to_snake": "def camel_to_snake(s):\n    return ''.join('_'+c.lower() if c.isupper() else c for c in s)",
    "digits_sum": "def digits_sum(n):\n    return sum(map(int,str(abs(n))))",
    "transpose": "def transpose(m):\n    return [list(r) for r in zip(*m)]",
    "median": "def median(xs):\n    s=sorted(xs);n=len(s)\n    return s[n//2] if n%2 else (s[n//2-1]+s[n//2])/2",
    "caesar": "def caesar(s,k):\n    o=''\n    for c in s:\n        if c.isalpha():\n            b=ord('A') if c.isupper() else ord('a'); o+=chr((ord(c)-b+k)%26+b)\n        else: o+=c\n    return o",
}


# ---- measurement ------------------------------------------------------------------------------

def run_tier(chat, tasks: list[Task], conc: int) -> list[Graded]:
    conc = max(1, conc)
    with ThreadPoolExecutor(max_workers=conc) as ex:
        return list(ex.map(lambda t: grade_one(t, chat), tasks))


def p99(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(0.99 * (len(s) - 1))))]


def measure_arm(nvml, h, chat, tasks: list[Task], arm: str, limit_w: int,
                conc_scale: float = 1.0) -> dict:
    nvml.set_power_limit_mw(h, limit_w * 1000)
    out = {"arm": arm, "limit_w": limit_w, "tiers": {}}
    for tier in TIERS:
        ts = [t for t in tasks if t.tier == tier]
        e0, t0 = nvml.total_energy_mj(h), time.perf_counter()
        g = run_tier(chat, ts, int(CONCURRENCY[tier] * conc_scale))
        e1, wall = nvml.total_energy_mj(h), time.perf_counter() - t0
        out["tiers"][tier] = {"n": len(g), "pass": sum(x.ok for x in g), "joules": (e1 - e0) / 1000,
                              "wall_s": wall, "p99_s": p99([x.latency_s for x in g]),
                              "tokens": sum(x.tokens for x in g),
                              "fails": [x.id for x in g if not x.ok][:20],
                              "agent": [x.detail for x in g][:3] if tier == "complex" else None}
    return out


def run_block(nvml, h, chat, tasks: list[Task], cap_w: int, *, reps: int = 1,
              clock=None, log=print, conc_scale: float = 1.0) -> dict:
    """ABBA order per rep (stock, cap, cap, stock) so drift hits both arms the same."""
    stock_w = nvml.power_default_limit_mw(h) // 1000
    arms = []
    try:
        for r in range(reps):
            for arm in ("stock", "cap", "cap", "stock"):
                if clock is not None:
                    clock.reset() if arm == "stock" else clock.apply_current()
                arms.append(measure_arm(nvml, h, chat, tasks, arm, stock_w if arm == "stock"
                                        else cap_w, conc_scale))
                log(f"tiers rep {r + 1} {arm} done")
    finally:
        nvml.set_power_limit_mw(h, stock_w * 1000)
        if clock is not None:
            clock.reset()
    return {"cap_w": cap_w, "stock_w": stock_w, "arms": arms}


def summarize(block: dict, *, gpu_usd_per_h: float = GPU_USD_PER_H, mock: bool = False) -> dict:
    label = "MOCK" if mock else "MEASURED"
    rows = []
    for tier in TIERS:
        agg = {}
        for arm in ("stock", "cap"):
            xs = [a["tiers"][tier] for a in block["arms"] if a["arm"] == arm]
            n = sum(x["n"] for x in xs)
            j = sum(x["joules"] for x in xs)
            wall = sum(x["wall_s"] for x in xs)
            wh = j / 3600 / n if n else 0.0
            agg[arm] = {"n": n, "pass_pct": 100 * sum(x["pass"] for x in xs) / n if n else 0.0,
                        "wh_per_task": wh, "ml_per_task": wh / 1000 * WUE_L_PER_KWH * 1000,
                        "usd_energy_per_1k": wh * 1000 / 1000 * TARIFF_USD_PER_KWH,
                        "usd_gpu_time_per_1k": (wall / n) * 1000 / 3600 * gpu_usd_per_h
                        if n else 0.0,
                        "p99_s": statistics.median([x["p99_s"] for x in xs]) if xs else 0.0}
        s, c = agg["stock"], agg["cap"]
        drop = s["pass_pct"] - c["pass_pct"]
        slow = 100 * (c["p99_s"] / s["p99_s"] - 1) if s["p99_s"] else 0.0
        why = ([f"pass rate fell {drop:.1f} pts (> {MAX_DROP_PTS:g})"] if drop > MAX_DROP_PTS
               else []) + ([f"p99 +{slow:.0f}% (> {P99_SLACK_PCT:g}%)"] if slow > P99_SLACK_PCT
                           else [])
        rows.append({"tier": tier, "label": label, "stock": s, "cap": c,
                     "energy_change_pct": 100 * (c["wh_per_task"] / s["wh_per_task"] - 1)
                     if s["wh_per_task"] else 0.0,
                     "pass_drop_pts": drop, "p99_change_pct": slow,
                     "gate": "pass" if not why else "blocked", "why_not": why})
    return {"cap_w": block["cap_w"], "label": label,
            "assumptions": {"wue_l_per_kwh": WUE_L_PER_KWH,
                            "tariff_usd_per_kwh": TARIFF_USD_PER_KWH,
                            "gpu_usd_per_h": gpu_usd_per_h,
                            "note": "Wh is GPU-only NVML energy; mL and $ are ESTIMATES"},
            "tiers": rows}


def compare_quant(full: dict, quant: dict) -> dict:
    """Keep or reject a quantized model (e.g. FP8) against full precision (BF16).

    Both summaries come from the same tiers at the same caps. Per tier, the quantized stock arm
    must stay within the same gate as a cap (pass rate -2 pts, p99 +10%). One blocked tier
    rejects the quantized model: it is never shipped on the strength of the easy tiers."""
    rows = []
    for f, q in zip(full["tiers"], quant["tiers"]):
        for arm in ("stock", "cap"):
            a, b = f[arm], q[arm]
            drop = a["pass_pct"] - b["pass_pct"]
            slow = 100 * (b["p99_s"] / a["p99_s"] - 1) if a["p99_s"] else 0.0
            why = ([f"pass rate fell {drop:.1f} pts"] if drop > MAX_DROP_PTS else []) + \
                  ([f"p99 +{slow:.0f}%"] if slow > P99_SLACK_PCT else [])
            rows.append({"tier": f["tier"], "arm": arm, "pass_drop_pts": drop,
                         "p99_change_pct": slow,
                         "energy_change_pct": 100 * (b["wh_per_task"] / a["wh_per_task"] - 1)
                         if a["wh_per_task"] else 0.0, "gate": "pass" if not why else "blocked",
                         "why_not": why})
    keep = all(r["gate"] == "pass" for r in rows)
    return {"decision": "keep" if keep else "reject", "label": quant.get("label"), "rows": rows}


def table(summary: dict) -> str:
    lab = summary["label"]
    lines = [f"tiers-v1 at {summary['cap_w']} W vs stock ({lab}; mL and $ are ESTIMATES)",
             f"{'tier':8} {'Wh/task':>9} {'change':>7} {'mL/task':>8} {'$en/1k':>7} "
             f"{'$gpu/1k':>8} {'pass s->c':>12} {'p99':>6}  gate"]
    for r in summary["tiers"]:
        c, s = r["cap"], r["stock"]
        lines.append(f"{r['tier']:8} {c['wh_per_task']:9.4f} {r['energy_change_pct']:+6.1f}% "
                     f"{c['ml_per_task']:8.3f} {c['usd_energy_per_1k']:7.3f} "
                     f"{c['usd_gpu_time_per_1k']:8.3f} {s['pass_pct']:5.1f}->{c['pass_pct']:5.1f} "
                     f"{r['p99_change_pct']:+5.0f}%  {r['gate']}"
                     + (f" ({'; '.join(r['why_not'])})" if r["why_not"] else ""))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cap", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--url", default="http://127.0.0.1:8000/v1/chat/completions")
    ap.add_argument("--model-config", default="muse-glimmer-30b",
                    help=f"one of {sorted(MODELS)} or a JSON file (llama-3.1-8b is the fallback)")
    ap.add_argument("--clock", default=None)
    ap.add_argument("--per-tier", type=int, default=0,
                    help="trim: at most N items per tier (0 = all); used when time runs short")
    ap.add_argument("--compare-to", type=Path, default=None,
                    help="a full-precision tiers_summary.json: also write the keep/reject verdict")
    ap.add_argument("--require-reasoning", type=Path, default=None, metavar="VLLM_LOG",
                    help="shakedown: fail (exit 6) if this vLLM log shows the reasoning-parser "
                         "warning or replies carry no reasoning_content")
    ap.add_argument("--gpu-usd-per-h", type=float, default=GPU_USD_PER_H)
    a = ap.parse_args(argv)
    cfg = model_config(a.model_config)
    tasks = apply_model(build(), cfg)
    if a.per_tier:
        tasks = [t for tier in TIERS for t in [x for x in tasks if x.tier == tier][:a.per_tier]]
    if a.fake:
        from wattnot.proof.fake import FakeNvml
        nvml = FakeNvml()
        h = nvml.device_handle(0)
        chat = FakeChat(nvml, h, tasks)
    else:
        from wattnot.control.nvml_ctl import CapGuard
        from wattnot.probe import open_nvml
        nvml = open_nvml()
        h = nvml.device_handle(0)
        CapGuard(nvml, h).__enter__()  # restores the saved limit on exit and on SIGTERM
        chat = Chat(a.url, cfg["model"])
        chat.context_limit = cfg.get("max_model_len")
    clock = None
    if a.clock and a.clock != "base":
        from wattnot.proof.clocks import ClockLock
        clock = ClockLock(nvml, h)
        prof = a.clock
        clock.apply_current = lambda: clock.apply(prof)  # type: ignore[attr-defined]
    a.out.mkdir(parents=True, exist_ok=True)
    block = run_block(nvml, h, chat, tasks, a.cap, reps=a.reps, clock=clock,
                      conc_scale=cfg["concurrency_scale"])
    block["model"] = cfg
    if clock is not None:
        block["clock"] = {"profile": a.clock, "applied": clock.available, "reason": clock.reason}
    (a.out / "tiers_raw.json").write_text(json.dumps(block, indent=1, default=str))
    summ = summarize(block, gpu_usd_per_h=a.gpu_usd_per_h, mock=a.fake)
    summ["model"] = cfg["model"]
    (a.out / "tiers_summary.json").write_text(json.dumps(summ, indent=1))
    print(table(summ))
    if a.compare_to is not None:
        v = compare_quant(json.loads(a.compare_to.read_text()), summ)
        (a.out / "quant_verdict.json").write_text(json.dumps(v, indent=1))
        print(f"{cfg['name']} vs full precision: {v['decision'].upper()}")
    if a.require_reasoning is not None:
        log = a.require_reasoning.read_text(errors="replace") if a.require_reasoning.exists() else ""
        chk = reasoning_check(log, getattr(chat, "replies", 0), getattr(chat, "reasoning_seen", 0))
        (a.out / "reasoning_check.json").write_text(json.dumps(chk, indent=1))
        print(f"reasoning check: {'PASS' if chk['ok'] else 'FAIL'} {'; '.join(chk['why'])}")
        if not chk["ok"]:
            return 6
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
