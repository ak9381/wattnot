"""Continuous quality monitor and canary for the routing saver (no with/without A/B run).

A ``shadow_rate`` share of requests answered by a cheaper tier is re-sent, off the request path,
to the baseline tier. Each pair gives two things:
* a MEASURED baseline/small completion-token ratio, used to price the baseline for every request
* an agreement check (same final number, or same normalised text). If agreement over the last
  ``min_samples`` pairs drops below ``min_agreement``, routing is rolled back to the baseline tier
  (the next requests skip the small tier) and an event is logged. It re-arms only by restart.
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
from pathlib import Path

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def agree(a: str, b: str) -> bool:
    na, nb = _NUM.findall(a or ""), _NUM.findall(b or "")
    if na and nb:
        return na[-1] == nb[-1]
    norm = lambda s: " ".join(re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split())  # noqa: E731
    return bool(norm(a)) and norm(a) == norm(b)


# Sequential side by side on one GPU: the with-arm sets read (NVML energy, mJ) and sync, so each
# shadow call to stock runs alone and its energy is bracketed and taken out of the arm's window.
SHADOW_METER: dict = {"read": None, "sync": False, "excluded_mj": 0, "calls": 0}


def tool_key(calls) -> list:
    """Tool calls as sorted (name, canonical args) pairs, for comparing two agent turns."""
    out = []
    for c in calls or []:
        f = (c or {}).get("function") or {}
        try:
            a = json.dumps(json.loads(f.get("arguments") or "{}"), sort_keys=True)
        except (TypeError, ValueError):
            a = str(f.get("arguments"))
        out.append((f.get("name"), a))
    return sorted(out)


def agree_turn(a_text: str, a_tools, b_text: str, b_tools) -> bool:
    """agree() for text answers; for agent turns that answer with tool calls (empty text), the
    same tools with the same arguments count as agreeing. Text-only compare used to call every
    tool-call turn a disagreement (empty vs empty)."""
    if a_tools or b_tools:
        return tool_key(a_tools) == tool_key(b_tools)
    return agree(a_text, b_text)


class Monitor:
    def __init__(self, cfg: dict, upstream, base_tier: dict, path: Path, rng=None, sync: bool = False,
                 gate=None, gate_key: str | None = None):
        self.gate, self.gate_key = gate, gate_key
        b = cfg["baseline"]
        self.rate = float(b.get("shadow_rate", 0.0))
        self.min_agree = float(b.get("min_agreement", 0.8))
        self.min_n = int(b.get("min_samples", 10))
        self.up, self.base, self.path = upstream, base_tier, Path(path)
        self.rng = rng or random.Random()
        self.sync = sync
        self.pairs: list[dict] = []
        self.rolled_back = False
        self.events: list[dict] = []
        self._lock = threading.Lock()

    def ratio(self) -> float | None:
        with self._lock:
            sm = sum(p["small_out"] for p in self.pairs)
            bg = sum(p["base_out"] for p in self.pairs)
        return round(bg / sm, 3) if sm and bg else None

    def maybe_shadow(self, body: dict, small: dict, receipt_id: str) -> bool:
        if self.rate <= 0:   # monitor off: no shadows, so the gate has no pairs either
            return False
        warm = self.gate is not None and self.gate.warming(self.gate_key, small.get("applied"))
        if not warm and self.rng.random() >= self.rate:
            return False
        t = threading.Thread(target=self._shadow, args=(body, small, receipt_id), daemon=True)
        t.start()
        if self.sync or SHADOW_METER["sync"]:
            t.join()
        return True

    def _shadow(self, body: dict, small: dict, receipt_id: str) -> None:
        read = SHADOW_METER["read"]
        e0 = read() if read else None
        try:
            self._shadow_call(body, small, receipt_id)
        finally:
            if e0 is not None:
                SHADOW_METER["excluded_mj"] += read() - e0
                SHADOW_METER["calls"] += 1

    def _shadow_call(self, body: dict, small: dict, receipt_id: str) -> None:
        from wattnot.agentwaste.server import sanitize, tier_body
        clean, _, _ = sanitize(body)
        try:
            resp = self.up(tier_body(clean, self.base), {"x-wattnot-shadow": "1"})
        except Exception:  # noqa: BLE001  shadow never affects users
            return
        u = resp.get("usage") or {}
        msg = (resp.get("choices") or [{}])[0].get("message") or {}
        text = str(msg.get("content") or "")
        pair = {"ts": round(time.time(), 3), "receipt_id": receipt_id,
                "small_out": int(small["usage"].get("completion_tokens") or 0),
                "base_out": int(u.get("completion_tokens") or 0),
                "base_in": int(u.get("prompt_tokens") or 0),
                "small_in": int(small["usage"].get("prompt_tokens") or 0),
                "agree": agree_turn(small.get("text", ""), small.get("tool_calls"), text, msg.get("tool_calls")), "label": "MEASURED (shadow pair)"}
        if self.gate is not None:
            self.gate.record_pair(self.gate_key, pair["small_in"] + pair["small_out"],
                                  pair["base_in"] + pair["base_out"], pair["agree"], small.get("applied"))
        with self._lock:
            self.pairs.append(pair)
            recent = self.pairs[-self.min_n:]
            if (not self.rolled_back and len(recent) >= self.min_n
                    and sum(p["agree"] for p in recent) / len(recent) < self.min_agree):
                self.rolled_back = True
                self.events.append({"ts": pair["ts"], "event": "rollback", "saver": "routing",
                                    "agreement": round(sum(p["agree"] for p in recent) / len(recent), 3)})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(pair) + "\n")

    def status(self) -> dict:
        with self._lock:
            n = len(self.pairs)
            ag = sum(p["agree"] for p in self.pairs)
            recent = self.pairs[-self.min_n:]
        return {"shadow_rate": self.rate, "pairs": n,
                "agreement": round(ag / n, 3) if n else None,
                "recent_agreement": round(sum(p["agree"] for p in recent) / len(recent), 3) if recent else None,
                "completion_ratio": self.ratio(), "rolled_back": self.rolled_back, "events": list(self.events),
                "shadow_usd_note": "shadow calls are billed; they are excluded from 'actual' savings math"}
