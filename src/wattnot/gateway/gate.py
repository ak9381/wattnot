"""Per-model saver gate: a saver stays on for a model only while it is no worse than stock there.

Two savers can cost more than they save on some models:
* routing: the "small" tier is a different body (e.g. reasoning_effort=low). On some served models
  (Muse Glimmer on vLLM, 4 Nov H100 run) low effort makes the model think MORE, so the small tier
  uses more tokens than the baseline. Shadow pairs (same request, baseline tier) measure this.
* the agent guard's nudges and injected ``done`` tool: +~50 prompt tokens per agent call (a
  "repeated N times, call done" note). They only pay back when the guard actually ends a loop. If
  it never does over ``done_window`` agent calls, or a shadow pair loses, the guard stops changing
  requests for that model (token and step budgets still stop runaway runs).

A gate decision is logged as an event and lasts until restart. Energy here follows tokens
(J = tokens x J/token), so "no worse on tokens" is the per-request energy test; the NVML trace
remains the MEASURED check for a whole run.
"""
from __future__ import annotations

import threading
import time


class SaverGate:
    def __init__(self, min_pairs: int = 3, min_agreement: float = 0.8, done_window: int = 10,
                 tolerance: float = 1.0):
        self.min_pairs, self.min_agree, self.done_window, self.tol = (
            int(min_pairs), float(min_agreement), int(done_window), float(tolerance))
        self.off: dict[tuple[str, str], dict] = {}
        self.pairs: dict[str, list[dict]] = {}
        self.done: dict[str, dict] = {}
        self.events: list[dict] = []
        self._lock = threading.Lock()

    def allow(self, saver: str, model: str | None) -> bool:
        return (saver, str(model)) not in self.off

    def _switch_off(self, saver: str, model: str, why: str, **ev) -> None:
        if (saver, model) in self.off:
            return
        e = {"ts": round(time.time(), 3), "event": "saver_off", "saver": saver, "model": model,
             "why": why, **ev}
        self.off[(saver, model)] = e
        self.events.append(e)

    @staticmethod
    def _key(model, applied) -> str:
        return f"{model}|{'+'.join(sorted(applied or ['routing']))}"

    def warming(self, model: str | None, applied: list[str] | None = None) -> bool:
        """True until this mix of savers has min_pairs shadow pairs on this model (shadow every call
        till then), so a saver that only starts applying later (after another is switched off) is
        calibrated on its own too."""
        with self._lock:
            return len(self.pairs.get(self._key(model, applied), [])) < self.min_pairs

    def record_pair(self, model: str | None, small_total: int, base_total: int, agree: bool,
                    applied: list[str] | None = None) -> None:
        """One shadow pair: WattNot's request vs the same request at stock. A losing pair counts
        against every saver that changed that request (routing, and the agent guard if it nudged)."""
        m = str(model)
        with self._lock:
            ps = self.pairs.setdefault(self._key(m, applied), [])
            ps.append({"small": int(small_total), "base": int(base_total), "agree": bool(agree)})
            if all(not self.allow(sv, m) for sv in (applied or ["routing"])):
                return
            # one pair at 1.5x stock or worse is already a clear loss: stop paying for more calibration
            clear_loss = int(small_total) > 1.5 * int(base_total)
            if len(ps) < self.min_pairs and not clear_loss:
                return
            recent = ps[-max(self.min_pairs, 10):]
            sm, bs = sum(p["small"] for p in recent), sum(p["base"] for p in recent)
            ag = sum(p["agree"] for p in recent) / len(recent)
            why = ("WattNot's request used more tokens than stock" if sm > bs * self.tol
                   else "answers differ from stock" if ag < self.min_agree else None)
            if why:
                for sv in (applied or ["routing"]):
                    self._switch_off(sv, m, why, wattnot_tokens=sm, stock_tokens=bs,
                                     agreement=round(ag, 3), pairs=len(recent))

    def record_agent_call(self, model: str | None, done_injected: bool, guard_stopped: bool) -> None:
        if not done_injected and not guard_stopped:
            return
        m = str(model)
        with self._lock:
            d = self.done.setdefault(m, {"calls": 0, "stops": 0})
            d["calls"] += 1
            d["stops"] += int(bool(guard_stopped))
            if d["calls"] >= self.done_window and d["stops"] == 0:
                self._switch_off("agent_waste", m, "agent guard changed requests and never ended a loop",
                                 calls=d["calls"])

    def status(self) -> dict:
        with self._lock:
            return {"off": [dict(v) for v in self.off.values()], "events": list(self.events),
                    "pairs": {k: len(p) for k, p in self.pairs.items()},
                    "agent_calls": {m: dict(v) for m, v in self.done.items()},
                    "rule": "a saver stays on for a model only while it is no worse than stock there"}
