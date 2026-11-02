"""Baseten upstream for the agent-waste proxy (LangChain pass on a hosted API).

The proxy hands us an OpenAI chat body; we send it to Baseten's Model API with the paid
workspace key (cloud.INFERENCE_KEY_ENVS, never the dead BASETEN_API_KEY). Effort routing uses
reasoning_effort (low first, medium on a step-up) instead of vLLM's chat_template_kwargs.

Every call is reserved before it goes out and settled after, in a spend ledger with a hard stop
(default $2). Cost is MEASURED from Baseten's usage tokens x list price; energy is an ESTIMATE
from tokens (cloud.energy). The key is never logged or returned.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from wattnot.cloud import MODEL, chat_completions_url
from wattnot.cloud.energy import wh_range
from wattnot.cloud.pricing import Rates, cost_usd
from wattnot.reasoning import apply_to_body
from wattnot.spend.guard import TOKEN, Guard, HardStop, Ledger, Price

LOW, STEP_UP = "low", "medium"


def tiers(model: str = MODEL) -> list[dict]:
    """Saver-on routing on one hosted model: effort low, step-up to medium."""
    return [{"name": "spark-effort-low", "model": model, "body_extra": {"reasoning_effort": LOW}},
            {"name": "spark-effort-medium", "model": model, "reasoning": True,
             "body_extra": {"reasoning_effort": STEP_UP}}]


def make_guard(ledger_path: str | Path, hard_usd: float = 2.0, rates: Rates = Rates()) -> Guard:
    price = Price(TOKEN, usd_in_per_m=rates.input_per_m, usd_out_per_m=rates.output_per_m)
    return Guard("baseten-langchain", price, Ledger(ledger_path), hard_usd=hard_usd,
                 soft_usd=round(hard_usd * 0.9, 4), lag_margin_usd=round(min(0.1, hard_usd * 0.05), 4))


def worst_usd(rates: Rates, body: dict) -> float:
    chars = len(json.dumps(body.get("messages"))) + len(json.dumps(body.get("tools") or []))
    return cost_usd(rates, chars // 3 + 64, 0, int(body.get("max_tokens") or 2048))


class BasetenUpstream:
    """upstream(body, headers) -> response dict. Thread-safe counters per arm."""

    def __init__(self, key: str, guard: Guard, *, model: str = MODEL, base_url: str | None = None,
                 baseline_effort: str = STEP_UP, rates: Rates = Rates(), timeout: float = 300.0,
                 max_tokens_floor: int = 1024):
        if not key:
            raise ValueError("no Baseten inference key")
        self._key, self.guard, self.model, self.rates = key, guard, model, rates
        self.url = chat_completions_url(base_url) if base_url else chat_completions_url()
        self.baseline_effort, self.timeout, self.floor = baseline_effort, timeout, max_tokens_floor
        self.lock = threading.Lock()
        self.arm = "none"
        self.stats: dict = {}
        self.stopped: str | None = None

    def body(self, body: dict) -> dict:
        b = dict(body)
        b["model"] = self.model
        b.pop("chat_template_kwargs", None)          # vLLM-only knob; Baseten uses effort
        mt = int(b.pop("max_completion_tokens", 0) or b.get("max_tokens") or 2048)
        b["max_tokens"] = max(mt, self.floor)
        apply_to_body(b, {"reasoning_effort": b.get("reasoning_effort") or self.baseline_effort})
        return b

    def _add(self, u: dict, usd: float, effort: str, err: bool = False):
        with self.lock:
            s = self.stats.setdefault(self.arm, {"calls": 0, "errors": 0, "prompt_tokens": 0,
                                                 "cached_tokens": 0, "completion_tokens": 0,
                                                 "usd": 0.0, "efforts": {}})
            s["calls"] += 1
            s["errors"] += int(err)
            s["efforts"][effort] = s["efforts"].get(effort, 0) + 1
            for k in ("prompt_tokens", "completion_tokens"):
                s[k] += int(u.get(k, 0) or 0)
            s["cached_tokens"] += int((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0)
            s["usd"] = round(s["usd"] + usd, 6)

    def __call__(self, body: dict, headers: dict) -> dict:
        b = self.body(body)
        effort = b.get("reasoning_effort")
        worst = worst_usd(self.rates, b)
        try:
            rid = self.guard.reserve(worst, item=f"{self.arm}:{effort}")
        except HardStop as e:
            self.stopped = str(e)
            raise
        req = urllib.request.Request(self.url, data=json.dumps(b).encode(), headers={
            "Content-Type": "application/json", "Authorization": f"Api-Key {self._key}"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                d = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            self.guard.settle(rid, worst, error=e.code, arm=self.arm)   # a failed call may bill
            self._add({}, worst, effort, err=True)
            if e.code in (401, 402, 403):
                self.stopped = f"HTTP {e.code}"
                self.guard.mark_stop("hard", f"HTTP {e.code}")
            raise OSError(f"Baseten HTTP {e.code}") from None
        except BaseException:
            self.guard.settle(rid, worst, error="exception", arm=self.arm)
            self._add({}, worst, effort, err=True)
            raise
        u = d.get("usage") or {}
        usd = cost_usd(self.rates, int(u.get("prompt_tokens", 0) or 0),
                       int((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0),
                       int(u.get("completion_tokens", 0) or 0))
        self.guard.settle(rid, usd, arm=self.arm, effort=effort, latency_s=round(time.time() - t0, 3),
                          prompt_tokens=u.get("prompt_tokens"), completion_tokens=u.get("completion_tokens"))
        self._add(u, usd, effort)
        return d

    def arm_summary(self, arm: str) -> dict:
        s = dict(self.stats.get(arm) or {"calls": 0, "prompt_tokens": 0, "cached_tokens": 0,
                                         "completion_tokens": 0, "usd": 0.0})
        lo, hi = wh_range(s["prompt_tokens"], s["cached_tokens"], s["completion_tokens"])
        s["usd_label"] = "MEASURED tokens x list price"
        s["energy_wh"] = {"low": round(lo, 4), "high": round(hi, 4), "mid": round((lo + hi) / 2, 4),
                          "label": "ESTIMATE (from tokens)"}
        return s
